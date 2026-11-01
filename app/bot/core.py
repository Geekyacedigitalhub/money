"""机器人内核：所有渠道共享的对话逻辑。

职责边界很清楚：

* 收到一条 ``ChatMessage``，决定下一步做什么（生成 / 发布 / 改设置）。
* 需要长耗时工作时，丢进线程池，并把结果通过 ``notify`` 推送回渠道。
* 只产出 ``Reply``，不直接调用任何平台的发送 API。

因此 Telegram 适配器和网页适配器都只是很薄的壳。
"""

from __future__ import annotations

import os
import threading
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor

from loguru import logger

from app.bot import captions as caption_service
from app.bot import generation as generation_service
from app.bot import publisher as publisher_service
from app.bot.config import BotSettings, load_settings, publish_account_ready
from app.bot.models import (
    ALL_PLATFORMS,
    Button,
    Caption,
    ChatMessage,
    Job,
    JobState,
    Platform,
    Reply,
    Session,
    parse_platform,
)

# 长驻进程的内存边界：任务与待推送回复都只保留最近的记录。
_MAX_JOBS = 200
_MAX_PENDING_PER_CHAT = 50
_MAX_PENDING_CHATS = 200

HELP_TEXT = """\
🤖 **{name}**

Send me a topic and I'll write the script, voice it, subtitle it, cut it to
stock footage, and post it to Facebook, YouTube, TikTok and Instagram.

**Just type a topic:**
`Inflation in Nigeria explained in 60 seconds`

**Commands**
/help — this message
/platforms — choose where to post
/new — start a new video
/status — progress of the current job
/preview — resend the last video
/caption <text> — override the caption text
/cancel — stop the current job
/connect — check which platforms are really connected
/how — what each platform accepts (length, account, payouts)
/mode — show demo / live status

Tip: you can also **send me an MP4 directly** and I'll publish that instead.
"""


class BotCore:
    """渠道无关的机器人。一个进程内只需要一个实例。"""

    def __init__(
        self,
        settings: BotSettings | None = None,
        *,
        generator: Callable[..., generation_service.GenerationResult] | None = None,
        publisher: publisher_service.Publisher | None = None,
        executor: ThreadPoolExecutor | None = None,
    ) -> None:
        self.settings = settings or load_settings()
        self._generator = generator or generation_service.generate
        self._executor = executor or ThreadPoolExecutor(
            max_workers=max(1, self.settings.max_concurrent_jobs),
            thread_name_prefix="mpt-bot",
        )
        self._publisher = publisher or publisher_service.build_publisher(
            self.settings,
            receipt_dir=self._receipt_dir(),
        )
        self._sessions: dict[str, Session] = {}
        self._jobs: dict[str, Job] = {}
        self._pending: dict[str, list[Reply]] = {}
        self._active_by_chat: dict[str, str] = {}
        self._cancel_events: dict[str, threading.Event] = {}
        self._lock = threading.RLock()
        self._notifier: Callable[[str, Reply], None] | None = None

    # ------------------------------------------------------------------
    # 基础设施
    # ------------------------------------------------------------------

    def _receipt_dir(self) -> str | None:
        try:
            from app.utils import utils

            return os.path.join(utils.storage_dir("bot", create=True), "receipts")
        except Exception:  # noqa: BLE001 - 演示/测试环境可能没有 storage 目录
            return None

    def set_notifier(self, notifier: Callable[[str, Reply], None] | None) -> None:
        """注册异步推送回调（Telegram 用）。网页端可以轮询 ``drain_pending``。"""
        self._notifier = notifier

    def drain_pending(self, chat_id: str) -> list[Reply]:
        with self._lock:
            return self._pending.pop(chat_id, [])

    def _emit(self, chat_id: str, reply: Reply) -> None:
        with self._lock:
            queue = self._pending.setdefault(chat_id, [])
            queue.append(reply)
            # 网页端可能长时间不轮询（关掉标签页），保留最近若干条即可，
            # 否则一个常驻进程会被离线会话慢慢撑大。
            if len(queue) > _MAX_PENDING_PER_CHAT:
                del queue[:-_MAX_PENDING_PER_CHAT]
            while len(self._pending) > _MAX_PENDING_CHATS:
                self._pending.pop(next(iter(self._pending)), None)
        if self._notifier is not None:
            try:
                self._notifier(chat_id, reply)
            except Exception as exc:  # noqa: BLE001 - 推送失败不能影响生成
                logger.warning(f"bot notifier failed: {exc}")

    def session(self, chat_id: str) -> Session:
        with self._lock:
            session = self._sessions.get(chat_id)
            if session is None:
                session = Session(
                    chat_id=chat_id,
                    platforms=list(self.settings.default_platforms),
                    auto_publish=self.settings.auto_publish,
                )
                self._sessions[chat_id] = session
            return session

    def job(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def jobs(self) -> list[Job]:
        with self._lock:
            return sorted(self._jobs.values(), key=lambda item: item.created_at, reverse=True)

    def _prune_locked(self) -> None:
        """丢弃最旧的已结束任务，保持长驻进程的内存与磁盘引用可控。"""
        if len(self._jobs) <= _MAX_JOBS:
            return
        terminal = sorted(
            (job for job in self._jobs.values() if job.is_terminal),
            key=lambda item: item.created_at,
        )
        for job in terminal:
            if len(self._jobs) <= _MAX_JOBS:
                break
            # 只清理不再被会话引用、也不在运行中的记录。
            if any(
                session.last_job_id == job.id for session in self._sessions.values()
            ):
                continue
            self._jobs.pop(job.id, None)
            self._cancel_events.pop(job.id, None)

    @property
    def live(self) -> bool:
        return getattr(self._publisher, "live", False)

    @property
    def publisher_name(self) -> str:
        return str(getattr(self._publisher, "name", "unknown"))

    # ------------------------------------------------------------------
    # 入口
    # ------------------------------------------------------------------

    def handle(self, message: ChatMessage) -> list[Reply]:
        """处理一条消息，立即返回需要同步发送的回复。"""
        if not self.settings.allows(message.user_id or message.chat_id):
            return [
                Reply(
                    text=(
                        "🚫 This bot is restricted to an allow-list.\n"
                        f"Your user id is `{message.user_id or message.chat_id}` — "
                        "add it to [bot] allowed_users in config.toml."
                    )
                )
            ]

        session = self.session(message.chat_id)

        if message.callback:
            return self._handle_callback(session, message.callback)

        if message.attachment:
            return self._handle_attachment(session, message)

        text = (message.text or "").strip()
        if not text:
            return [Reply(text="Send me a topic, or /help for commands.", buttons=self._platform_keyboard(session))]

        if text.startswith("/"):
            return self._handle_command(session, text)
        return self._start_generation(session, text)

    # ------------------------------------------------------------------
    # 命令
    # ------------------------------------------------------------------

    def _handle_command(self, session: Session, text: str) -> list[Reply]:
        parts = text.split(maxsplit=1)
        command = parts[0].lstrip("/").split("@")[0].lower()
        argument = parts[1].strip() if len(parts) > 1 else ""

        if command in {"start", "help"}:
            return [
                Reply(
                    text=HELP_TEXT.format(name=self.settings.display_name),
                    buttons=self._help_keyboard(session),
                )
            ]
        if command in {"platforms", "platform", "apps"}:
            return [self._platforms_reply(session)]
        if command == "new":
            return [Reply(text="🎬 What should the video be about? Send me a topic.")]
        if command == "status":
            return [self._status_reply(session)]
        if command in {"preview", "video"}:
            return [self._preview_reply(session)]
        if command == "caption":
            if not argument:
                return [Reply(text="Usage: /caption your caption text here")]
            return self._set_caption(session, argument)
        if command == "cancel":
            return [self._cancel(session)]
        if command in {"mode", "demo"}:
            return [self._mode_reply()]
        if command in {"jobs", "history"}:
            return [self._jobs_reply()]
        if command in {"connect", "connections", "check"}:
            return [self._connect_reply()]
        if command in {"how", "rules", "limits"}:
            from app.bot import delivery

            return [Reply(text=delivery.format_guides())]
        return [
            Reply(
                text=f"Unknown command `/{command}`.\n\n"
                + HELP_TEXT.format(name=self.settings.display_name)
            )
        ]

    # ------------------------------------------------------------------
    # 平台选择
    # ------------------------------------------------------------------

    def _platform_keyboard(self, session: Session) -> list[list[Button]]:
        selected = session.selected()
        rows: list[list[Button]] = []
        row: list[Button] = []
        for platform in ALL_PLATFORMS:
            row.append(
                Button(
                    label=platform.button_label(platform in selected),
                    data=f"pl:{platform.value}",
                )
            )
            if len(row) == 2:
                rows.append(row)
                row = []
        if row:
            rows.append(row)
        rows.append([Button(label="✅ Done", data="pl:done")])
        return rows

    def _help_keyboard(self, session: Session) -> list[list[Button]]:
        rows = self._platform_keyboard(session)
        rows.append([Button(label="🎬 New video", data="new")])
        return rows

    def _platforms_reply(self, session: Session) -> Reply:
        selected = session.selected()
        lines = ["📡 **Where should I post?** Tap to toggle.", ""]
        for platform, status in publisher_service.readiness_report(self.settings):
            mark = "✅" if platform in selected else "⬜"
            lines.append(f"{mark} {platform.emoji} **{platform.label}** — {status}")
        lines.append("")
        if not selected:
            lines.append("⚠️ No platform selected — pick at least one.")
        elif not self.live:
            lines.append(
                "🧪 Publishing is in **simulation** mode, so nothing leaves your machine. "
                "Add Upload-Post credentials in config.toml to go live."
            )
        return Reply(text="\n".join(lines), buttons=self._platform_keyboard(session))

    # ------------------------------------------------------------------
    # 生成
    # ------------------------------------------------------------------

    def _start_generation(self, session: Session, topic: str) -> list[Reply]:
        with self._lock:
            active_id = self._active_by_chat.get(session.chat_id)
            if active_id:
                active = self._jobs.get(active_id)
                if active and not active.is_terminal:
                    return [
                        Reply(
                            text=(
                                f"⏳ I'm still working on *{active.topic}* "
                                f"({active.progress}%).\n"
                                "Send /cancel to stop it, or /status to check."
                            )
                        )
                    ]
            job = Job(chat_id=session.chat_id, topic=topic, platforms=list(session.platforms))
            self._jobs[job.id] = job
            self._active_by_chat[session.chat_id] = job.id
            session.last_job_id = job.id
            cancel = threading.Event()
            self._cancel_events[job.id] = cancel
            self._prune_locked()

        mode = "🧪 demo" if (self.settings.demo_mode or self.settings.is_demo) else "live"
        self._executor.submit(self._run_generation, job.id, cancel)

        target = ", ".join(item.label for item in session.selected()) or "no platform yet"
        return [
            Reply(
                text=(
                    f"🎬 **Working on:** {topic}\n"
                    f"Mode: {mode} · Target: {target}\n\n"
                    "I'll send the video here when it's done. /cancel to stop."
                ),
                job_id=job.id,
            )
        ]

    def _run_generation(self, job_id: str, cancel: threading.Event) -> None:
        job = self.job(job_id)
        if job is None:
            return
        job.touch(state=JobState.generating, progress=1, message="Starting")

        def on_progress(percent: int, message: str) -> None:
            # 只在进度明显变化时推送，避免刷屏。
            if percent - job.progress >= 20:
                job.touch(progress=percent, message=message)

        try:
            result = self._generator(
                job_id=job_id,
                topic=job.topic,
                script=job.script,
                settings=self.settings,
                on_progress=on_progress,
                cancel=cancel,
            )
        except generation_service.GenerationError as exc:
            self._fail_job(job, str(exc))
            return
        except Exception as exc:  # noqa: BLE001
            logger.exception(f"bot generation failed: {exc}")
            self._fail_job(job, f"{type(exc).__name__}: {exc}")
            return

        if cancel.is_set():
            self._fail_job(job, "cancelled")
            return

        session = self.session(job.chat_id)
        selections = job.platform_objects() or session.selected()
        job.platforms = [item.value for item in selections]
        job.captions = caption_service.generate_captions(
            topic=job.topic,
            script=result.script,
            platforms=selections or list(ALL_PLATFORMS),
            language=self.settings.caption_language,
        )
        job.touch(
            state=JobState.ready,
            progress=100,
            video=result.video,
            poster=result.poster,
            script=result.script,
            simulated=result.simulated,
            task_id=result.task_id,
            message="Ready to publish",
        )

        warnings = result.warnings or []
        if warnings:
            job.error = ""  # 警告不等于失败
            job.results = {"_warnings": " | ".join(warnings)}

        with self._lock:
            self._active_by_chat.pop(job.chat_id, None)

        # 自动发布（默认关闭）：只有真实发布器可用时才自动发，否则把预览
        # 交给用户确认，避免“看起来发了其实没发”。
        if session.auto_publish and self.live:
            self._publish(job, session, auto=True)
            return

        self._emit(job.chat_id, self._preview_publish_reply(job))

    def _fail_job(self, job: Job, reason: str) -> None:
        friendly = self._friendly_error(reason)
        job.touch(state=JobState.failed, error=friendly, message="Failed")
        with self._lock:
            self._active_by_chat.pop(job.chat_id, None)
        self._emit(
            job.chat_id,
            Reply(
                text=f"❌ **Generation failed**\n\n{reason}\n\n{friendly}".strip(),
                buttons=[[
                    Button(label="🔁 Try again", data=f"regen:{job.id}"),
                    Button(label="📡 Platforms", data="pl:refresh"),
                ]],
            ),
        )

    @staticmethod
    def _friendly_error(reason: str) -> str:
        lowered = reason.lower()
        hints = []
        if "api key" in lowered or "api_key" in lowered:
            hints.append(
                "Add the missing key under [app] in config.toml "
                "(LLM key + pexels_api_keys), or turn on [bot] demo_mode = true."
            )
        if "ffmpeg" in lowered:
            hints.append("Install FFmpeg, or set app.ffmpeg_path in config.toml.")
        if "rate limit" in lowered or "429" in lowered:
            hints.append("A provider is rate-limiting you — wait a minute and retry.")
        if "pexels" in lowered:
            hints.append("Check app.pexels_api_keys is a non-empty array of strings.")
        return ("\n💡 " + " ".join(hints)) if hints else ""

    # ------------------------------------------------------------------
    # 预览与发布
    # ------------------------------------------------------------------

    def _preview_publish_reply(self, job: Job) -> Reply:
        selections = job.platform_objects()
        lines = [f"🎬 **{job.topic}**", ""]

        if job.simulated or self.settings.demo_mode or self.settings.is_demo:
            lines.append("🧪 Demo render — this is a placeholder, not real footage.")
        if not self.live:
            lines.append(
                "🧪 Publishing is simulated — tapping publish will *not* post publicly."
            )

        for platform in selections:
            caption = job.captions.get(platform.value)
            if caption is None:
                continue
            lines.append(f"**{platform.emoji} {platform.label}**")
            lines.append(f"_{caption.title}_")
            if caption.description and caption.description != caption.title:
                body = caption.description.strip()
                lines.append(body if len(body) <= 240 else body[:237] + "…")
            if caption.hashtags:
                lines.append(" ".join(caption.hashtags))
            lines.append("")

        warnings = (job.results or {}).get("_warnings")
        if warnings:
            lines.append(f"⚠️ {warnings}")
            lines.append("")

        lines.append("Publish, redo, or discard?")
        buttons = [
            [Button(label="🔁 Regenerate video", data=f"regen:{job.id}")],
            [Button(label="✏️ New captions", data=f"cap:{job.id}")],
            [Button(label="📡 Change platforms", data="pl:refresh")],
        ]
        publish_label = "🚀 Publish now" if self.live else "🧪 Simulate publish"
        buttons.insert(
            0,
            [Button(label=publish_label, data=f"pub:{job.id}"),
             Button(label="🗑 Discard", data=f"drop:{job.id}")],
        )
        return Reply(text="\n".join(lines).strip(), video=job.video, poster=job.poster,
                     buttons=buttons, job_id=job.id)

    def _publish(self, job: Job, session: Session, auto: bool = False) -> None:
        platforms = job.platform_objects() or session.selected()
        if not platforms:
            self._emit(
                job.chat_id,
                Reply(
                    text="⚠️ Pick at least one platform first.",
                    buttons=self._platform_keyboard(session),
                ),
            )
            return
        if not job.video or not os.path.isfile(job.video):
            self._emit(
                job.chat_id,
                Reply(
                    text="⚠️ The video file is gone. Regenerate it.",
                    buttons=[[Button(label="🔁 Regenerate", data=f"regen:{job.id}")]],
                ),
            )
            return

        job.touch(state=JobState.publishing, message="Publishing", results={})
        self._emit(
            job.chat_id,
            Reply(
                text=(
                    f"🚀 Publishing to {', '.join(item.label for item in platforms)}…"
                    + (" (simulated)" if not self.live else "")
                ),
                job_id=job.id,
            ),
        )
        self._executor.submit(self._run_publish, job.id, platforms, auto)

    def _run_publish(self, job_id: str, platforms: list[Platform], auto: bool) -> None:
        job = self.job(job_id)
        if job is None:
            return
        captions = job.captions or {}
        try:
            results = self._publisher.publish(
                video_path=job.video or "",
                captions=captions,
                platforms=platforms,
                settings=self.settings,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception(f"publish crashed: {exc}")
            job.touch(state=JobState.failed, error=str(exc))
            self._emit(
                job.chat_id,
                Reply(text=f"❌ Publishing failed: {exc}"),
            )
            return

        ok = [item for item in results if item.success]
        failed = [item for item in results if not item.success]
        job.results = {
            item.platform.value: (item.url or "ok") if item.success else item.detail
            for item in results
        }
        simulated = any(item.simulated for item in results)
        job.touch(
            state=JobState.published if ok else JobState.failed,
            simulated=simulated,
            message="Published" if ok else "Publish failed",
        )

        lines: list[str] = []
        if ok:
            title = "🧪 Simulated publish complete" if simulated else "✅ Published"
            lines.append(f"{title}: " + ", ".join(item.platform.label for item in ok))
            if simulated:
                lines.append(
                    "_Nothing was uploaded. Add Upload-Post credentials to go live._"
                )
            else:
                # 直接给出帖子链接：这是"到底发出去了没有"的唯一硬证据。
                lines.append("")
                for item in ok:
                    if item.url:
                        lines.append(f"{item.platform.emoji} {item.platform.label} — {item.url}")
                    else:
                        lines.append(
                            f"{item.platform.emoji} {item.platform.label} — posted "
                            "(no link returned; check the app)"
                        )
        if failed:
            lines.append("")
            lines.append("❌ Failed:")
            for item in failed:
                detail = item.detail
                lowered = detail.lower()
                if "skipped" in lowered or "no account" in lowered:
                    detail = "not connected to this profile — run /connect"
                lines.append(f"• {item.platform.emoji} {item.platform.label} — {detail}")

        buttons = [[
            Button(label="🔁 Make another", data="new"),
            Button(label="📡 Platforms", data="pl:refresh"),
        ]]
        if failed:
            buttons.insert(0, [Button(label="🚀 Retry failed", data=f"pub:{job.id}")])
        self._emit(job.chat_id, Reply(text="\n".join(lines).strip(), buttons=buttons))

    # ------------------------------------------------------------------
    # 回调（按钮点击）
    # ------------------------------------------------------------------

    def _handle_callback(self, session: Session, data: str) -> list[Reply]:
        kind, _, payload = data.partition(":")

        if kind == "pl":
            if payload == "done":
                selected = session.selected()
                if not selected:
                    return [Reply(text="⚠️ Pick at least one platform.")]
                return [
                    Reply(
                        text="✅ Posting to: "
                        + ", ".join(item.label for item in selected)
                        + "\n\nSend me a topic to make a video."
                    )
                ]
            if payload == "refresh":
                return [self._platforms_reply(session)]
            platform = parse_platform(payload)
            if platform is None:
                return [Reply(text="Unknown platform.")]
            now_selected = session.toggle(platform)
            lines = [
                f"{'✅ Added' if now_selected else '➖ Removed'} {platform.emoji} {platform.label}",
                "",
                "Selected: "
                + (", ".join(item.label for item in session.selected()) or "none"),
            ]
            return [Reply(text="\n".join(lines), buttons=self._platform_keyboard(session))]

        if kind == "conn":
            # 只有用户明确点“重新检查”才花掉一次 API 调用。
            from app.bot import doctor

            doctor.clear_cache()
            return [self._connect_reply()]

        if kind == "new":
            return [Reply(text="🎬 What should the video be about? Send me a topic.")]

        job_id = payload
        job = self.job(job_id)

        if kind == "pub":
            if job is None:
                return [Reply(text="That job no longer exists.")]
            if job.state is JobState.publishing:
                return [Reply(text="⏳ Already publishing…")]
            self._publish(job, session)
            return []

        if kind == "regen":
            if job is None:
                return [Reply(text="That job no longer exists.")]
            topic = job.topic
            with self._lock:
                self._jobs.pop(job.id, None)
            return self._start_generation(session, topic)

        if kind == "test":
            platform = parse_platform(payload)
            if platform is None:
                return [Reply(text="Unknown platform.")]
            return self._start_test_post(session.chat_id, platform)

        if kind == "cap":
            if job is None:
                return [Reply(text="That job no longer exists.")]
            platforms = job.platform_objects() or session.selected()
            job.captions = caption_service.generate_captions(
                topic=job.topic,
                script=job.script,
                platforms=platforms,
                language=self.settings.caption_language,
            )
            return [self._preview_publish_reply(job)]

        if kind == "drop":
            if job is not None:
                job.touch(state=JobState.failed, error="discarded", message="Discarded")
            return [Reply(text="🗑 Discarded. Send me a new topic whenever you're ready.")]

        return [Reply(text=HELP_TEXT.format(name=self.settings.display_name))]

    # ------------------------------------------------------------------
    # 手动上传的视频
    # ------------------------------------------------------------------

    def _handle_attachment(self, session: Session, message: ChatMessage) -> list[Reply]:
        path = message.attachment or ""
        if not os.path.isfile(path):
            return [Reply(text="⚠️ I couldn't read that file. Try sending it again.")]
        size_mb = os.path.getsize(path) / (1024 * 1024)
        if size_mb > self.settings.max_upload_mb:
            return [
                Reply(
                    text=(
                        f"⚠️ That file is {size_mb:.0f} MB, over the "
                        f"{self.settings.max_upload_mb} MB limit."
                    )
                )
            ]

        topic = os.path.splitext(os.path.basename(path))[0].replace("_", " ").strip()
        job = Job(
            chat_id=session.chat_id,
            topic=topic or "Uploaded video",
            state=JobState.ready,
            progress=100,
            video=path,
            script="",
            platforms=list(session.platforms),
            message="Ready to publish",
        )
        job.captions = caption_service.generate_captions(
            topic=job.topic,
            script="",
            platforms=job.platform_objects() or list(ALL_PLATFORMS),
            language=self.settings.caption_language,
        )
        with self._lock:
            self._jobs[job.id] = job
            session.last_job_id = job.id
        return [self._preview_publish_reply(job)]

    # ------------------------------------------------------------------
    # 其它交互
    # ------------------------------------------------------------------

    def _status_reply(self, session: Session) -> Reply:
        job = self.job(session.last_job_id) if session.last_job_id else None
        if job is None:
            return Reply(text="No jobs yet. Send me a topic to get started.")
        return Reply(
            text=(
                f"**{job.topic}**\n"
                f"State: {job.state.value} · {job.progress}%\n"
                f"{job.message or ''}"
                + (f"\n\n❌ {job.error}" if job.error and job.state is JobState.failed else "")
            ).strip(),
            video=job.video if job.state in {JobState.ready, JobState.published} else None,
            buttons=self._preview_publish_reply(job).buttons if job.video else [],
            job_id=job.id,
        )

    def _preview_reply(self, session: Session) -> Reply:
        job = self.job(session.last_job_id) if session.last_job_id else None
        if job is None or not job.video:
            return Reply(text="Nothing to preview yet. Send me a topic first.")
        return self._preview_publish_reply(job)

    def _set_caption(self, session: Session, text: str) -> list[Reply]:
        job = self.job(session.last_job_id) if session.last_job_id else None
        if job is None:
            return [Reply(text="No video to caption yet.")]
        platforms = job.platform_objects() or session.selected()
        job.captions = {
            platform.value: caption_service.adapt(
                Caption(title=text.strip()[:120], description=text.strip(), hashtags=[]),
                platform,
            )
            for platform in platforms
        }
        return [self._preview_publish_reply(job)]

    def _start_test_post(self, chat_id: str, platform: Platform) -> list[Reply]:
        """发一条真实的测试帖，证明链路端到端可用。"""
        from app.bot import doctor

        report = doctor.check_connection(self.settings)
        status = report.status_for(platform)
        if not status.connected or status.needs_reauth:
            return [
                Reply(
                    text=(
                        f"⚠️ {platform.label} is not connected yet.\n"
                        f"Connect it here: {doctor.DASHBOARD_MANAGE_USERS}"
                    )
                )
            ]

        notice = Reply(
            text=(
                f"🧪 Rendering a 6-second test clip and posting it to "
                f"**{platform.label}** — this is a REAL public post. "
                "It will appear on your profile shortly."
            )
        )
        self._executor.submit(self._run_test_post, chat_id, platform)
        return [notice]

    def _run_test_post(self, chat_id: str, platform: Platform) -> None:
        from app.bot import doctor

        try:
            result = doctor.send_test_post(self.settings, platform)
        except Exception as exc:  # noqa: BLE001
            logger.exception(f"test post crashed: {exc}")
            self._emit(
                chat_id,
                Reply(text=f"❌ Test post to {platform.label} failed: {exc}"),
            )
            return

        lines = [result.summary]
        if result.success and result.url:
            lines.append("")
            lines.append("Open it to confirm it is live.")
        elif result.success:
            lines.append("")
            lines.append("No URL came back, so check the platform app directly.")
        self._emit(chat_id, Reply(text="\n".join(lines)))

    def _cancel(self, session: Session) -> Reply:
        active_id = self._active_by_chat.get(session.chat_id)
        job = self.job(active_id) if active_id else None
        if job is None or job.is_terminal:
            return Reply(text="Nothing is running right now.")
        event = self._cancel_events.get(job.id)
        if event is not None:
            event.set()
        job.touch(state=JobState.failed, error="cancelled", message="Cancelled")
        with self._lock:
            self._active_by_chat.pop(session.chat_id, None)
        return Reply(text="🛑 Cancelled.")

    def _connect_reply(self) -> Reply:
        """逐平台检查连接状态，并给出可直接点的测试按钮。"""
        from app.bot import doctor

        report = doctor.check_connection(self.settings)
        buttons: list[list[Button]] = []
        for status in report.platforms:
            if status.connected and not status.needs_reauth:
                buttons.append(
                    [
                        Button(
                            label=f"🧪 Send test to {status.platform.label}",
                            data=f"test:{status.platform.value}",
                        )
                    ]
                )
        buttons.append([Button(label="🔄 Re-check now", data="conn:refresh")])
        if not report.config_ready:
            buttons.append([Button(label="📡 Platforms", data="pl:refresh")])
        return Reply(text=doctor.format_report(report), buttons=buttons)

    def _mode_reply(self) -> Reply:
        ready, reason = publish_account_ready()
        lines = ["⚙️ **Bot mode**", ""]
        lines.append(
            "Generation: 🧪 demo (placeholder renders)"
            if (self.settings.demo_mode or self.settings.is_demo)
            else f"Generation: 🎬 live (LLM + TTS + {self.settings.video_source})"
        )
        lines.append(
            f"Publishing: 🚀 live via {getattr(self._publisher, 'name', 'upload-post')}"
            if ready
            else f"Publishing: 🧪 simulated — {reason}"
        )
        lines.append("")
        lines.append("Change these in config.toml under `[bot]` and `[app]`.")
        return Reply(text="\n".join(lines))

    def _jobs_reply(self) -> Reply:
        jobs = self.jobs()[:8]
        if not jobs:
            return Reply(text="No jobs yet.")
        lines = ["🗂 **Recent jobs**", ""]
        for job in jobs:
            icon = {
                JobState.published: "✅",
                JobState.ready: "🎬",
                JobState.failed: "❌",
                JobState.publishing: "🚀",
                JobState.generating: "⏳",
                JobState.queued: "⏳",
            }[job.state]
            lines.append(f"{icon} {job.topic[:48]} — {job.state.value}")
        return Reply(text="\n".join(lines))


_CORE: BotCore | None = None
_CORE_LOCK = threading.Lock()


def build_core(settings: BotSettings | None = None, **kwargs) -> BotCore:
    """进程内单例，避免每个请求都新建线程池和发布器。"""
    global _CORE
    with _CORE_LOCK:
        if _CORE is None:
            _CORE = BotCore(settings, **kwargs)
        return _CORE


def reset_core() -> None:
    """测试用：清掉单例。"""
    global _CORE
    with _CORE_LOCK:
        _CORE = None


def iter_platforms() -> Iterable[Platform]:
    return ALL_PLATFORMS
