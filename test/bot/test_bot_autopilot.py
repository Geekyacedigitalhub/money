"""自动运营（autopilot）的排期、配额、选题与发布行为。"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from app.bot import autopilot as autopilot_service
from app.bot.autopilot import Autopilot, parse_times, resolve_timezone
from app.bot.config import BotSettings
from app.bot.models import ChatMessage, JobState, Reply

LAGOS = ZoneInfo("Africa/Lagos")


def _settings(**overrides):
    base = {
        "demo_mode": True,
        "autopilot_times": ("08:00", "13:00", "19:00"),
        "autopilot_max_per_day": 3,
        "autopilot_min_gap_minutes": 0,
        "autopilot_niche": "earning online with AI tools",
        "autopilot_timezone": "Africa/Lagos",
        "autopilot_publish": True,
        "default_platforms": ["facebook", "instagram"],
    }
    base.update(overrides)
    return BotSettings(**base)


class FakeJob:
    def __init__(self, job_id: str, *, simulated: bool = True) -> None:
        self.id = job_id
        self.topic = "topic"
        self.state = JobState.ready
        self.progress = 100
        self.video = "/tmp/final.mp4"
        self.poster = ""
        self.script = ""
        self.simulated = simulated
        self.error = ""
        self.results: dict[str, str] = {}
        self.caption = ""

    def platform_objects(self):
        return []


class FakeCore:
    """够用的假内核：生成立刻 ready，发布立刻 published。"""

    def __init__(self, *, simulated: bool = True) -> None:
        self.settings = _settings()
        self.sessions: dict[str, SimpleNamespace] = {}
        self._jobs: dict[str, FakeJob] = {}
        self.published_calls = 0
        self.simulated = simulated
        self.drained: list[str] = []

    def session(self, chat_id: str):
        if chat_id not in self.sessions:
            self.sessions[chat_id] = SimpleNamespace(
                chat_id=chat_id, platforms=[], auto_publish=True
            )
        return self.sessions[chat_id]

    def handle(self, message: ChatMessage):
        if message.callback:
            job_id = message.callback.split(":", 1)[1]
            job = self._jobs[job_id]
            self.published_calls += 1
            job.state = JobState.published
            job.results = {"facebook": "https://facebook.com/reel/1"}
            return [Reply(text="published")]
        job = FakeJob(f"job-{len(self._jobs) + 1}", simulated=self.simulated)
        self._jobs[job.id] = job
        return [Reply(text="working", job_id=job.id)]

    def job(self, job_id: str):
        return self._jobs.get(job_id)

    def drain_pending(self, chat_id: str):
        self.drained.append(chat_id)


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    # 任何真实发布凭据检查都当作“未配置”，测试不会真的往外发。
    monkeypatch.setattr(autopilot_service, "publish_account_ready", lambda: (False, "test"))


def _autopilot(tmp_path, core=None, settings=None, **kwargs) -> Autopilot:
    return Autopilot(
        core=core,
        settings=settings or _settings(),
        state_path=str(tmp_path / "state.json"),
        topics_path=str(tmp_path / "topics.txt"),
        **kwargs,
    )


def test_parse_times_sorts_and_drops_garbage():
    assert [item.strftime("%H:%M") for item in parse_times(["19:30", "8:00", "nope", "25:00"])] == [
        "08:00",
        "19:30",
    ]


def test_unknown_timezone_falls_back_to_utc():
    assert str(resolve_timezone("Mars/Base")) == "UTC"
    assert str(resolve_timezone("Africa/Lagos")) == "Africa/Lagos"


def test_next_run_uses_the_next_configured_slot(tmp_path):
    now = datetime(2026, 11, 1, 9, 30, tzinfo=LAGOS)
    auto = _autopilot(tmp_path, clock=lambda: now)
    assert auto.next_run() == datetime(2026, 11, 1, 13, 0, tzinfo=LAGOS)


def test_next_run_rolls_to_tomorrow_after_the_last_slot(tmp_path):
    now = datetime(2026, 11, 1, 20, 0, tzinfo=LAGOS)
    auto = _autopilot(tmp_path, clock=lambda: now)
    assert auto.next_run() == datetime(2026, 11, 2, 8, 0, tzinfo=LAGOS)


def test_paused_means_no_next_run(tmp_path):
    auto = _autopilot(tmp_path)
    auto.state.paused = True
    assert auto.next_run() is None


def test_daily_cap_pushes_to_the_next_day(tmp_path):
    now = datetime(2026, 11, 1, 7, 0, tzinfo=LAGOS)
    auto = _autopilot(tmp_path, clock=lambda: now)
    auto.state.day = "2026-11-01"
    auto.state.count = 3
    assert auto.next_run() == datetime(2026, 11, 2, 8, 0, tzinfo=LAGOS)


def test_interval_mode_spaces_runs(tmp_path):
    settings = _settings(autopilot_interval_minutes=60, autopilot_times=())
    now = datetime(2026, 11, 1, 7, 20, tzinfo=LAGOS)
    auto = _autopilot(tmp_path, settings=settings, clock=lambda: now)
    assert auto.next_run() == datetime(2026, 11, 1, 8, 0, tzinfo=LAGOS)


def test_min_gap_protects_against_restart_double_posting(tmp_path):
    settings = _settings(autopilot_min_gap_minutes=180)
    now = datetime(2026, 11, 1, 8, 5, tzinfo=LAGOS)
    auto = _autopilot(tmp_path, settings=settings, clock=lambda: now)
    auto.state.last_run = datetime(2026, 11, 1, 8, 3, tzinfo=LAGOS).isoformat()
    assert auto.next_run() == datetime(2026, 11, 1, 13, 0, tzinfo=LAGOS)


def test_builtin_topics_are_used_when_the_file_is_missing(tmp_path):
    auto = _autopilot(tmp_path)
    topics = auto.topics()
    assert len(topics) >= 5
    assert all("your niche" not in item for item in topics)
    assert "earning online with AI tools" in topics[0]


def test_own_topics_win_and_rotate(tmp_path):
    auto = _autopilot(tmp_path)
    auto.add_topics(["Topic A", "Topic B"])
    assert auto.topics() == ["Topic A", "Topic B"]
    assert auto.next_topic() == "Topic A"
    auto.state.cursor = 1
    assert auto.next_topic() == "Topic B"
    auto.state.cursor = 2  # 循环回去，永远有内容
    assert auto.next_topic() == "Topic A"


def test_add_topics_skips_duplicates(tmp_path):
    auto = _autopilot(tmp_path)
    auto.add_topics(["Topic A"])
    assert auto.add_topics(["topic a", "Topic B"]) == 2


def test_state_survives_a_restart(tmp_path):
    auto = _autopilot(tmp_path)
    auto.state.cursor = 4
    auto.state.day = "2026-11-01"
    auto.state.count = 2
    auto.save()

    revived = _autopilot(tmp_path)
    assert revived.state.cursor == 4
    assert revived.state.count == 2


def test_rehearsal_renders_but_never_publishes(tmp_path):
    core = FakeCore(simulated=True)
    auto = _autopilot(tmp_path, core=core)
    result = auto.run_once("a topic")

    assert result["ok"] is True
    assert result["published"] is False
    assert "rehearsal" in result["note"]
    assert core.published_calls == 0
    assert auto.state.count == 1
    assert auto.state.cursor == 1
    assert core.drained == ["autopilot"]


def test_demo_video_is_not_published_even_in_live_mode(tmp_path, monkeypatch):
    monkeypatch.setattr(autopilot_service, "publish_account_ready", lambda: (True, ""))
    core = FakeCore(simulated=True)
    settings = _settings(demo_mode=False)
    auto = _autopilot(tmp_path, core=core, settings=settings)

    result = auto.run_once("a topic")
    assert auto.mode() == "live"
    assert result["published"] is False
    assert core.published_calls == 0


def test_live_mode_publishes_and_records_links(tmp_path, monkeypatch):
    monkeypatch.setattr(autopilot_service, "publish_account_ready", lambda: (True, ""))
    core = FakeCore(simulated=False)
    settings = _settings(demo_mode=False)
    auto = _autopilot(tmp_path, core=core, settings=settings)

    result = auto.run_once("a topic")
    assert result["published"] is True
    assert result["results"]["facebook"].startswith("https://")
    assert core.published_calls == 1


def test_session_auto_publish_is_forced_off(tmp_path):
    core = FakeCore()
    auto = _autopilot(tmp_path, core=core)
    auto.run_once("a topic")
    assert core.session("autopilot").auto_publish is False


def test_run_once_respects_the_daily_cap(tmp_path):
    core = FakeCore()
    settings = _settings(autopilot_max_per_day=1)
    auto = _autopilot(tmp_path, core=core, settings=settings)

    assert auto.run_once("first")["ok"] is True
    second = auto.run_once("second")
    assert second["skipped"] is True
    assert "daily limit" in second["reason"]
    assert core.published_calls == 0


def test_no_publish_flag_keeps_it_in_rehearsal(tmp_path, monkeypatch):
    monkeypatch.setattr(autopilot_service, "publish_account_ready", lambda: (True, ""))
    core = FakeCore(simulated=False)
    auto = _autopilot(
        tmp_path, core=core, settings=_settings(demo_mode=False), publish=False
    )
    assert auto.mode() == "rehearsal"
    assert auto.run_once("a topic")["published"] is False


def test_status_is_json_friendly(tmp_path):
    auto = _autopilot(tmp_path)
    auto.add_topics(["Only topic"])
    status = auto.status()
    assert status["mode"] == "rehearsal"
    assert status["queue_size"] == 1
    assert status["remaining_today"] == 3
    assert status["next_topic"] == "Only topic"
    assert isinstance(status["runs"], list)
    assert status["timezone"] == "Africa/Lagos"


def test_record_carries_the_job_id_so_the_ui_can_show_the_video(tmp_path):
    core = FakeCore()
    auto = _autopilot(tmp_path, core=core)
    result = auto.run_once("a topic")
    assert result["job_id"] == "job-1"
