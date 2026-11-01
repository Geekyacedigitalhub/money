"""全自动运营：定时选题 → 生成成片 → 自动发布，不需要人一条条点。

设计上刻意做得很朴素：

* 一个线程，串行执行。视频生成本来就吃满 CPU，并发只会互相拖慢。
* 状态落在 ``storage/bot/autopilot/state.json``。进程重启后不会把已经发过的
  主题再发一遍，也不会把当日配额清零。
* 选题优先用用户自己的清单（``topics.txt``），清单为空时用内置的模板轮换，
  所以它永远不会“没内容可发”。
* 只要还没有真实发布凭据，就自动进入 *排练* 模式：生成 + 记录，但不假装
  发出去了。宁可不发，也不能让人以为发了。

    python -m app.bot.cli autopilot --once      # 立刻跑一轮
    python -m app.bot.cli autopilot             # 常驻，按计划跑
    python -m app.bot.cli serve --autopilot     # 一个进程里同时开网页和自动运营
"""

from __future__ import annotations

import json
import os
import threading
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime, timedelta, tzinfo
from datetime import time as clock_time
from typing import Any
from zoneinfo import ZoneInfo

from loguru import logger

from app.bot.config import BotSettings, load_settings, publish_account_ready
from app.bot.models import SYSTEM_USER, ChatMessage, JobState

DEFAULT_TIMES = ("08:00", "13:00", "19:00")
MAX_RUN_HISTORY = 60

# 没有自己的选题清单时用这些模板轮换。{niche} 由配置里的 autopilot_niche 填入，
# 角度列表保证相邻两天不撞题 —— YouTube 对“批量模板内容”很敏感。
# 标题统一用“主题：钩子”的结构，任何名词短语填进去都读得通。
TOPIC_TAILS = (
    "{n} mistakes to avoid",
    "how to start with no money",
    "{n} free tools I actually use",
    "the part nobody explains",
    "test this before you spend money",
    "{n} signs you are ready",
    "what I would do first",
    "the cheapest way to learn",
    "what changed in 2026",
)

ANGLES = (
    "",
    " (for beginners)",
    " (Nigeria edition)",
    " (phone only)",
    " (30 minutes a day)",
)




def parse_times(values: Any) -> list[clock_time]:
    """把 ``["08:00", "19:30"]`` 解析成 ``time`` 列表，非法值直接忽略。"""
    if isinstance(values, str):
        values = [part.strip() for part in values.replace(";", ",").split(",")]
    parsed: list[clock_time] = []
    for value in values or []:
        text = str(value).strip()
        if not text or ":" not in text:
            continue
        hour_text, _, minute_text = text.partition(":")
        try:
            hour, minute = int(hour_text), int(minute_text)
        except ValueError:
            continue
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            parsed.append(clock_time(hour=hour, minute=minute))
    return sorted(set(parsed))


def resolve_timezone(name: str) -> tzinfo:
    """留空用系统本地时区；时区名无效时退回 UTC，不让进程起不来。"""
    text = str(name or "").strip()
    if not text:
        return datetime.now().astimezone().tzinfo or UTC
    for candidate in (text, "UTC"):
        try:
            return ZoneInfo(candidate)
        except Exception as exc:  # noqa: BLE001 - 无效时区不是致命错误
            logger.debug(f"timezone {candidate!r} rejected: {exc}")
    return UTC
    return ZoneInfo("UTC")


@dataclass
class AutopilotState:
    """自动运营的持久状态。字段名保持稳定，方便人工查看。"""

    cursor: int = 0
    day: str = ""
    count: int = 0
    last_run: str = ""
    paused: bool = False
    runs: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AutopilotState:
        if not isinstance(data, dict):
            return cls()
        runs = data.get("runs")
        return cls(
            cursor=max(0, int(data.get("cursor", 0) or 0)),
            day=str(data.get("day") or ""),
            count=max(0, int(data.get("count", 0) or 0)),
            last_run=str(data.get("last_run") or ""),
            paused=bool(data.get("paused")),
            runs=[item for item in runs or [] if isinstance(item, dict)][-MAX_RUN_HISTORY:],
        )

    def roll_day(self, today: str) -> None:
        if self.day != today:
            self.day = today
            self.count = 0


def _storage_dir() -> str:
    from app.utils import utils

    path = os.path.join(utils.storage_dir("bot", create=True), "autopilot")
    os.makedirs(path, exist_ok=True)
    return path


class Autopilot:
    """把 BotCore 当作“生成 + 发布”的黑盒，按计划喂主题进去。"""

    def __init__(
        self,
        core: Any = None,
        settings: BotSettings | None = None,
        *,
        state_path: str = "",
        topics_path: str = "",
        publish: bool = True,
        dry_run: bool = False,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.settings = settings or load_settings()
        self._core = core
        self._dir = _storage_dir()
        self.state_path = state_path or os.path.join(self._dir, "state.json")
        self.topics_path = topics_path or os.path.join(self._dir, "topics.txt")
        self.publish = publish
        self.dry_run = dry_run
        self._tz = resolve_timezone(self.settings.autopilot_timezone)
        self._clock = clock or (lambda: datetime.now(self._tz))
        self._lock = threading.Lock()
        self.state = self._load_state()

    # ------------------------------------------------------------------
    # 持久化
    # ------------------------------------------------------------------

    def _load_state(self) -> AutopilotState:
        try:
            with open(self.state_path, encoding="utf-8") as handle:
                return AutopilotState.from_dict(json.load(handle))
        except FileNotFoundError:
            return AutopilotState()
        except (OSError, ValueError) as exc:
            logger.warning(f"autopilot state unreadable, starting fresh: {exc}")
            return AutopilotState()

    def save(self) -> None:
        payload = self.state.to_dict()
        tmp = f"{self.state_path}.tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
            os.replace(tmp, self.state_path)
        except OSError as exc:  # 写不进状态文件不应该让发布中断
            logger.warning(f"autopilot state not saved: {exc}")

    # ------------------------------------------------------------------
    # 选题
    # ------------------------------------------------------------------

    def _read_topic_file(self) -> list[str]:
        try:
            with open(self.topics_path, encoding="utf-8") as handle:
                lines = handle.readlines()
        except FileNotFoundError:
            return []
        except OSError as exc:
            logger.warning(f"autopilot topics unreadable: {exc}")
            return []
        return [
            line.strip()
            for line in lines
            if line.strip() and not line.strip().startswith("#")
        ]

    def _synthetic_topics(self) -> list[str]:
        niche = (self.settings.autopilot_niche or "your niche").strip()
        topics: list[str] = []
        for index, tail in enumerate(TOPIC_TAILS):
            angle = ANGLES[index % len(ANGLES)]
            number = 3 + (index % 4)
            hook = tail.format(n=number)
            topics.append(f"{niche}: {hook}{angle}")
        return topics

    def topics(self) -> list[str]:
        """用户清单优先；没有清单时用内置模板，永远有内容可发。"""
        return self._read_topic_file() or self._synthetic_topics()

    def next_topic(self) -> str:
        pool = self.topics()
        if not pool:
            return ""
        return pool[self.state.cursor % len(pool)]

    def add_topics(self, topics: list[str]) -> int:
        """把自己的选题追加到清单末尾，返回追加后的总数。"""
        incoming = [str(item).strip() for item in topics if str(item).strip()]
        if not incoming:
            return len(self.topics())
        existing = self._read_topic_file()
        seen = {item.lower() for item in existing}
        fresh = [item for item in incoming if item.lower() not in seen]
        with open(self.topics_path, "a", encoding="utf-8") as handle:
            if not existing and os.path.getsize(self.topics_path) == 0:
                handle.write("# One topic per line. Lines starting with # are ignored.\n")
            handle.writelines(f"{item}\n" for item in fresh)
        return len(self._read_topic_file())

    def remove_topic(self, index: int) -> list[str]:
        pool = self._read_topic_file()
        if 0 <= index < len(pool):
            pool.pop(index)
            with open(self.topics_path, "w", encoding="utf-8") as handle:
                handle.write("# One topic per line. Lines starting with # are ignored.\n")
                handle.writelines(f"{item}\n" for item in pool)
        return self._read_topic_file()

    # ------------------------------------------------------------------
    # 排期
    # ------------------------------------------------------------------

    @property
    def times(self) -> list[clock_time]:
        """配置里写死的发布时间；没配置就用早/午/晚三次。"""
        return parse_times(self.settings.autopilot_times) or [
            clock_time(hour=8),
            clock_time(hour=13),
            clock_time(hour=19),
        ]

    def _slots(self, day: date) -> list[clock_time]:
        """某一天里允许开跑的时刻。固定时间优先，否则按间隔均分。"""
        interval = max(0, int(self.settings.autopilot_interval_minutes))
        if not interval:
            return self.times
        slots: list[clock_time] = []
        minutes = 0
        while minutes < 24 * 60:
            slots.append(clock_time(hour=minutes // 60, minute=minutes % 60))
            minutes += interval
        return slots

    def next_run(self, now: datetime | None = None) -> datetime | None:
        """下一次该跑的时间；暂停返回 ``None``，当日配额用尽则顺延到明天。"""
        if self.state.paused:
            return None

        now = now or self._clock()
        if now.tzinfo is None:
            now = now.replace(tzinfo=self._tz)
        now = now.astimezone(self._tz)

        last: datetime | None = None
        if self.state.last_run:
            try:
                last = datetime.fromisoformat(self.state.last_run).astimezone(self._tz)
            except ValueError:
                last = None
        gap = timedelta(minutes=max(0, int(self.settings.autopilot_min_gap_minutes)))

        for offset in range(8):
            day = (now + timedelta(days=offset)).date()
            used_up = (
                day.isoformat() == self.state.day
                and self.state.count >= self.max_per_day
            )
            if used_up:
                continue
            for slot in self._slots(day):
                moment = datetime.combine(day, slot, tzinfo=self._tz)
                if moment <= now:
                    continue
                if last is not None and gap and moment - last < gap:
                    continue
                return moment
        return None

    @property
    def max_per_day(self) -> int:
        return max(1, int(self.settings.autopilot_max_per_day))

    # ------------------------------------------------------------------
    # 执行
    # ------------------------------------------------------------------

    @property
    def core(self) -> Any:
        if self._core is None:
            from app.bot.core import build_core

            self._core = build_core()
        return self._core

    def mode(self) -> str:
        """live = 直接发出去；manual = 只做视频，人工上传；rehearsal = 占位渲染。

        Path B 用的是 manual：视频是真的，只是发布这一步交给人 + 平台自带的
        免费定时工具，所以不能把它当成“排练”。
        """
        if self.dry_run or self.settings.demo_mode:
            return "rehearsal"
        if self.settings.delivery == "manual":
            return "manual"
        ready, _reason = publish_account_ready()
        return "live" if (ready and self.publish) else "rehearsal"

    def _wait(self, job_id: str, done: set[JobState], timeout: float) -> Any:
        deadline = time.monotonic() + timeout
        job = None
        while time.monotonic() < deadline:
            job = self.core.job(job_id)
            if job is None or job.state in done:
                return job
            time.sleep(1.5)
        return job

    def run_once(self, topic: str = "") -> dict[str, Any]:
        """生成一条视频（在 live 模式下再发布它），返回这一轮的记录。"""
        now = self._clock().astimezone(self._tz)
        today = now.date().isoformat()
        with self._lock:
            self.state.roll_day(today)
            if self.state.count >= self.max_per_day:
                return {
                    "ok": False,
                    "skipped": True,
                    "reason": f"daily limit reached ({self.max_per_day})",
                }

        topic = (topic or self.next_topic()).strip()
        if not topic:
            return {"ok": False, "skipped": True, "reason": "no topics configured"}

        core = self.core
        session = core.session("autopilot")
        platform_values = list(self.settings.default_platforms)
        session.platforms = platform_values
        # 自动运营自己决定何时发布，不依赖交互会话里的 auto_publish 开关。
        session.auto_publish = False

        logger.info(f"autopilot: generating '{topic}' ({self.mode()})")
        replies = core.handle(
            ChatMessage(chat_id="autopilot", text=topic, user_id=SYSTEM_USER)
        )
        job_id = next((reply.job_id for reply in replies if reply.job_id), "")
        if not job_id:
            reason = replies[0].text if replies else "no job created"
            return self._record(topic, "", {"ok": False, "error": reason}, today)

        job = self._wait(
            job_id,
            {JobState.ready, JobState.published, JobState.failed},
            timeout=max(120, self.settings.job_timeout_seconds),
        )
        if job is None or job.state is JobState.failed:
            return self._record(
                topic,
                job_id,
                {"ok": False, "error": getattr(job, "error", "") or "generation failed"},
                today,
            )

        record: dict[str, Any] = {
            "ok": True,
            "topic": topic,
            "video": job.video,
            "simulated": bool(job.simulated),
            "published": False,
            "results": {},
        }

        # demo 视频绝不自动发到真实账号上：那是占位画面，不是内容。
        if self.mode() == "manual" and not job.simulated:
            record["note"] = (
                "manual delivery — run `python -m app.bot.cli week` to package it, "
                "then upload with Meta Business Suite / YouTube Studio / TikTok"
            )
        elif self.mode() == "rehearsal" or job.simulated:
            record["note"] = (
                "rehearsal — video made but not published (demo render or "
                "publishing not configured)"
            )
        elif job.state is not JobState.published:
            core.handle(
                ChatMessage(chat_id="autopilot", callback=f"pub:{job.id}", user_id=SYSTEM_USER)
            )
            published = self._wait(
                job.id, {JobState.published, JobState.failed}, timeout=900
            )
            record["published"] = bool(published and published.state is JobState.published)
            record["results"] = dict(getattr(published, "results", {}) or {})
            if not record["published"]:
                record["error"] = getattr(published, "error", "") or "publish failed"
        else:
            record["published"] = True
            record["results"] = dict(job.results or {})

        # 交互式回复都塞在这个假 chat 里，取走以免长期占用内存。
        try:
            core.drain_pending("autopilot")
        except Exception as exc:  # noqa: BLE001 - 清理失败不影响发布结果
            logger.debug(f"autopilot pending cleanup skipped: {exc}")
        return self._record(topic, job_id, record, today)

    def _record(
        self, topic: str, job_id: str, record: dict[str, Any], today: str
    ) -> dict[str, Any]:
        now = self._clock().astimezone(self._tz)
        today = today or now.date().isoformat()
        with self._lock:
            self.state.roll_day(today)
            if not record.get("skipped"):
                self.state.count += 1
                self.state.cursor += 1
            self.state.last_run = now.isoformat(timespec="seconds")
            entry = {
                "at": self.state.last_run,
                "topic": topic,
                "job_id": job_id,
                "ok": bool(record.get("ok")),
                "published": bool(record.get("published")),
                "simulated": bool(record.get("simulated")),
                "error": str(record.get("error") or ""),
                "results": record.get("results") or {},
            }
            self.state.runs = (self.state.runs + [entry])[-MAX_RUN_HISTORY:]
            self.save()
        return {
            **record,
            "job_id": job_id,
            "chat_id": "autopilot",
            "ran_at": self.state.last_run,
        }

    # ------------------------------------------------------------------
    # 常驻循环
    # ------------------------------------------------------------------

    def loop(self, stop: threading.Event | None = None) -> None:
        stop = stop or threading.Event()
        cadence = (
            f"every {self.settings.autopilot_interval_minutes} min"
            if self.settings.autopilot_interval_minutes
            else "at " + ", ".join(item.strftime("%H:%M") for item in self.times)
        )
        logger.info(
            f"autopilot started ({self.mode()}) — {cadence}, max {self.max_per_day}/day"
        )
        while not stop.is_set():
            due = self.next_run()
            if due is None:
                # 暂停中：等一会儿再看，别把 CPU 烧在空转上。
                if stop.wait(60):
                    break
                continue
            now = self._clock().astimezone(self._tz)
            wait = (due - now).total_seconds()
            if wait > 0:
                logger.info(f"autopilot: next run at {due.isoformat(timespec='minutes')}")
                if stop.wait(min(wait, 60)):
                    break
                continue
            try:
                result = self.run_once()
                logger.info(f"autopilot result: {result.get('ok')} {result.get('topic', '')}")
            except Exception as exc:  # noqa: BLE001 - 常驻线程不能因为一次失败退出
                logger.exception(f"autopilot run failed: {exc}")
                self._record("", "", {"ok": False, "error": str(exc)}, self.state.day or "")
                stop.wait(60)

    def start_background(self, stop: threading.Event | None = None) -> threading.Thread:
        thread = threading.Thread(
            target=self.loop, args=(stop,), name="mpt-bot-autopilot", daemon=True
        )
        thread.start()
        return thread

    # ------------------------------------------------------------------
    # 状态
    # ------------------------------------------------------------------

    def status(self) -> dict[str, Any]:
        now = self._clock().astimezone(self._tz)
        due = self.next_run(now)
        pool = self.topics()
        return {
            "mode": self.mode(),
            "paused": self.state.paused,
            "interval_minutes": int(self.settings.autopilot_interval_minutes or 0),
            "times": [item.strftime("%H:%M") for item in self.times]
            if not self.settings.autopilot_interval_minutes
            else [],
            "max_per_day": self.max_per_day,
            "runs_today": self.state.count,
            "remaining_today": max(0, self.max_per_day - self.state.count),
            "next_run": due.isoformat(timespec="minutes") if due else "",
            "next_topic": self.next_topic(),
            "queue_size": len(pool),
            "custom_topics": bool(self._read_topic_file()),
            "last_run": self.state.last_run,
            "runs": self.state.runs[-5:],
            "timezone": str(self._tz),
            "blockers": preflight(self.settings),
        }


# ----------------------------------------------------------------------
# 上线前的体检
# ----------------------------------------------------------------------


def preflight(settings: BotSettings | None = None) -> list[dict[str, str]]:
    """列出“还不能真的自动发出去”的原因，每条都带修复方法。

    这是全自动模式里最重要的一个函数：没有它，用户只会看到“跑完了但没发”，
    然后以为是代码坏了。
    """
    from app.config import config as runtime_config

    blockers: list[dict[str, str]] = []
    settings = settings or load_settings()
    app_cfg = runtime_config.app

    if settings.demo_mode:
        blockers.append(
            {
                "key": "demo_mode",
                "why": "demo_mode = true: it renders placeholder slides, not real videos",
                "fix": "set [bot] demo_mode = false once your LLM + footage keys work",
            }
        )

    provider_id = str(app_cfg.get("llm_provider") or "").strip()
    key_field = f"{provider_id}_api_key" if provider_id else ""
    llm_key = str(app_cfg.get(key_field, "") or "").strip() if key_field else ""
    if not llm_key:
        blockers.append(
            {
                "key": "llm",
                "why": "no LLM key: it cannot write a script",
                "fix": (
                    f"set [app] {key_field or 'llm_provider + <provider>_api_key'} — "
                    "a free Gemini key (aistudio.google.com/app/apikey) works, or "
                    "run Ollama locally with llm_provider = \"ollama\" and pay nothing"
                ),
            }
        )

    pexels = app_cfg.get("pexels_api_keys") or []
    if isinstance(pexels, str):
        pexels = [pexels]
    if not [item for item in pexels if str(item).strip()]:
        blockers.append(
            {
                "key": "footage",
                "why": "no stock-footage key: there is nothing to put behind the voice",
                "fix": (
                    "get the free Pexels key at https://www.pexels.com/api/ and put "
                    "it in [app] pexels_api_keys"
                ),
            }
        )

    if settings.delivery == "manual":
        # 手动模式不需要 Upload-Post；只要确认他还知道东西放在哪。
        blockers.append(
            {
                "key": "delivery",
                "why": (
                    "delivery = manual: nothing is sent automatically — package with "
                    "`cli week` and schedule it yourself"
                ),
                "fix": "that is how Path B works; see SCHEDULING.md (free, no card)",
            }
        )
        return blockers

    ready, reason = publish_account_ready()
    if not ready:
        blockers.append(
            {
                "key": "publish",
                "why": f"Upload-Post is not configured ({reason})",
                "fix": "set [app] upload_post_enabled / upload_post_api_key / upload_post_username (see CONNECTING.md)",
            }
        )
    return blockers
