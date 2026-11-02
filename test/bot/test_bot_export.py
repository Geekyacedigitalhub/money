"""按周出片（Path B：人工上传）的打包逻辑。"""

from __future__ import annotations

import csv
import os
import zipfile

import pytest

from app.bot import export
from app.bot.config import BotSettings
from app.bot.models import Caption, ChatMessage, JobState, Reply


class FakeJob:
    def __init__(self, job_id: str, video: str) -> None:
        self.id = job_id
        self.state = JobState.ready
        self.video = video
        self.poster = ""
        self.error = ""
        self.captions = {
            "instagram": Caption(title="Title A", description="Desc A", hashtags=["a", "#b"]),
            "youtube": Caption(title="Title A", description="Title A", hashtags=[]),
        }

    @property
    def is_terminal(self) -> bool:
        return True


class FakeCore:
    def __init__(self, tmp_path, fail_on: int = -1) -> None:
        self.settings = BotSettings()
        self._jobs: dict[str, FakeJob] = {}
        self._tmp = tmp_path
        self._count = 0
        self._fail_on = fail_on

    def handle(self, message: ChatMessage):
        self._count += 1
        job_id = f"job{self._count}"
        if self._count == self._fail_on:
            return [Reply(text="cannot start")]
        video = self._tmp / f"{job_id}.mp4"
        video.write_bytes(b"fake mp4 bytes")
        self._jobs[job_id] = FakeJob(job_id, str(video))
        return [Reply(text="working", job_id=job_id)]

    def job(self, job_id: str):
        return self._jobs.get(job_id)


@pytest.fixture(autouse=True)
def _exports_in_tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(export, "exports_dir", lambda: str(tmp_path / "exports"))
    (tmp_path / "exports").mkdir()
    yield


def test_slugify_is_filename_safe():
    assert export.slugify("Why  your FIRST 100 customers... come from WhatsApp?!") == (
        "why-your-first-100-customers-come-from-whatsapp"
    )
    assert export.slugify("") == "video"
    assert len(export.slugify("x" * 200)) <= 48


def test_batch_writes_videos_captions_and_schedule(tmp_path):
    core = FakeCore(tmp_path)
    result = export.batch_from_topics(core, ["Topic one", "Topic two"], name="w1")

    assert result["count"] == 2
    assert result["failed"] == 0
    files = sorted(os.listdir(result["folder"]))
    assert "README.txt" in files
    assert "schedule.csv" in files
    assert len([f for f in files if f.endswith(".mp4")]) == 2
    assert len([f for f in files if f.endswith(".txt")]) == 3  # 2 captions + README

    with open(os.path.join(result["folder"], "schedule.csv"), encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert [row["file"] for row in rows] == [
        f for f in files if f.endswith(".mp4")
    ]
    assert all(row["slot"] for row in rows)


def test_caption_file_has_one_block_per_platform(tmp_path):
    core = FakeCore(tmp_path)
    result = export.batch_from_topics(core, ["Topic one"], name="w2")
    caption_file = next(
        (
            name
            for name in os.listdir(result["folder"])
            if name.endswith(".txt") and name != "README.txt"
        ),
        "",
    )
    with open(os.path.join(result["folder"], caption_file), encoding="utf-8") as handle:
        text = handle.read()

    assert "=== INSTAGRAM ===" in text
    assert "=== YOUTUBE ===" in text
    assert "#a #b" in text
    # 标题和正文相同时不重复两遍
    youtube_block = text.split("=== YOUTUBE ===")[1]
    assert youtube_block.count("Title A") == 1


def test_zip_contains_the_whole_folder(tmp_path):
    core = FakeCore(tmp_path)
    result = export.batch_from_topics(core, ["Topic one", "Topic two"], name="w3")

    assert result["zip"].endswith("w3.zip")
    with zipfile.ZipFile(result["zip"]) as bundle:
        names = bundle.namelist()
    assert any(name.endswith(".mp4") for name in names)
    assert "schedule.csv" in names
    assert "README.txt" in names


def test_failed_topics_are_reported_not_silently_dropped(tmp_path):
    core = FakeCore(tmp_path, fail_on=2)
    result = export.batch_from_topics(core, ["ok one", "broken", "ok two"], name="w4")

    assert result["count"] == 2
    assert result["failed"] == 1
    with open(
        os.path.join(result["folder"], "README.txt"), encoding="utf-8"
    ) as handle:
        readme = handle.read()
    assert "broken" in readme


def test_batch_name_is_predictable(tmp_path):
    core = FakeCore(tmp_path)
    result = export.batch_from_topics(core, ["Topic one"])
    assert result["name"].startswith("week-")


def test_resolve_zip_refuses_traversal(tmp_path):
    (tmp_path / "exports" / "ok.zip").write_bytes(b"PK")
    assert export.resolve_zip("ok.zip").endswith("ok.zip")
    assert export.resolve_zip("../config.toml") == ""
    assert export.resolve_zip("nope.zip") == ""
    assert export.resolve_zip("/etc/passwd") == ""


def test_manual_delivery_is_not_a_rehearsal(tmp_path, monkeypatch):
    """Path B：没有 Upload-Post 也不算“排练”，视频是真的。"""
    from app.bot import autopilot as autopilot_service

    settings = BotSettings(demo_mode=False, delivery="manual")
    auto = autopilot_service.Autopilot(
        settings=settings,
        state_path=str(tmp_path / "state.json"),
        topics_path=str(tmp_path / "topics.txt"),
    )
    assert auto.mode() == "manual"

    blockers = autopilot_service.preflight(settings)
    keys = {item["key"] for item in blockers}
    assert "publish" not in keys
    assert "delivery" in keys


def test_week_refuses_to_render_without_keys_when_demo_is_off(monkeypatch, capsys):
    """关了 demo_mode 却没有 Key，必须先停下，而不是跑几分钟再失败。"""
    from app.bot import cli
    from app.config import config

    monkeypatch.setitem(config.bot, "demo_mode", False)
    monkeypatch.setitem(config.app, "llm_provider", "moonshot")
    monkeypatch.setitem(config.app, "moonshot_api_key", "")
    monkeypatch.setitem(config.app, "pexels_api_keys", [])

    code = cli.main(["week", "--count", "1"])
    captured = capsys.readouterr()
    assert code == 2
    assert "Cannot make real videos yet" in captured.err
    assert "pexels" in captured.err.lower()


def test_week_still_works_in_demo_mode_without_keys(monkeypatch, capsys):
    """演示模式不需要任何 Key —— 这正是新手第一次跑通的方式。"""
    from app.bot import cli, export
    from app.config import config

    monkeypatch.setitem(config.bot, "demo_mode", True)
    monkeypatch.setitem(config.app, "pexels_api_keys", [])
    monkeypatch.setattr(
        export,
        "batch_from_topics",
        lambda *a, **k: {"count": 1, "folder": "x", "zip": "", "failed": 0, "rows": []},
    )

    assert cli.main(["week", "--count", "1"]) == 0
    assert "Cannot make real videos yet" not in capsys.readouterr().err


def test_local_footage_needs_no_api_key(tmp_path, monkeypatch):
    """Pexels 停发新 Key 时的备用方案：自己放片段进 storage/local_videos。"""
    from app.bot import autopilot as autopilot_service
    from app.bot import generation
    from app.config import config

    clips = tmp_path / "local_videos"
    clips.mkdir()
    (clips / "a.mp4").write_text("x")
    monkeypatch.setattr(generation, "_local_videos_dir", lambda: str(clips))
    monkeypatch.setitem(config.app, "video_source", "local")
    monkeypatch.setitem(config.app, "pexels_api_keys", [])
    monkeypatch.setitem(config.bot, "demo_mode", False)

    materials = generation._local_materials("local")
    assert [m.url for m in materials] == ["a.mp4"]

    keys = {item["key"] for item in autopilot_service.preflight()}
    assert "footage" not in keys


def test_local_footage_with_an_empty_folder_is_reported(tmp_path, monkeypatch):
    from app.bot import autopilot as autopilot_service
    from app.bot import generation
    from app.config import config

    empty = tmp_path / "local_videos"
    empty.mkdir()
    monkeypatch.setattr(generation, "_local_videos_dir", lambda: str(empty))
    monkeypatch.setitem(config.app, "video_source", "local")
    monkeypatch.setitem(config.bot, "demo_mode", False)

    blockers = {item["key"]: item for item in autopilot_service.preflight()}
    assert "footage" in blockers
    assert "local_videos" in blockers["footage"]["fix"]


def test_set_writes_each_key_into_the_right_section(tmp_path, monkeypatch):
    from app.bot import cli
    from app.config import config as runtime_config

    target = tmp_path / "config.toml"
    target.write_text('[app]\nllm_provider = "moonshot"\n\n[bot]\ndemo_mode = true\n')

    monkeypatch.setattr(cli, "_config_path", lambda: target)
    monkeypatch.setattr(runtime_config, "app", {}, raising=False)
    monkeypatch.setattr(runtime_config, "bot", {}, raising=False)

    cli.set_config_values(
        [
            "gemini_api_key=abc123",
            "pixabay_api_keys=111,222",
            "video_source=pixabay",
            "demo_mode=false",
        ]
    )

    text = target.read_text()
    app_part, bot_part = text.split("[bot]")
    assert 'gemini_api_key = "abc123"' in app_part
    assert 'pixabay_api_keys = ["111", "222"]' in app_part
    assert 'video_source = "pixabay"' in app_part
    assert "demo_mode = false" in bot_part
    assert "demo_mode = true" not in text


def test_video_source_follows_the_app_section(monkeypatch):
    """两处都写了 video_source，渲染器必须只认一个。"""
    from app.bot.config import load_settings
    from app.config import config as runtime_config

    monkeypatch.setitem(runtime_config.app, "video_source", "pixabay")
    monkeypatch.setitem(runtime_config.bot, "video_source", "pexels")
    assert load_settings().video_source == "pixabay"


def test_set_switches_llm_provider_to_the_key_that_was_given(tmp_path, monkeypatch):
    """填了 gemini_api_key 却还在查 moonshot_api_key，是最迷惑的一种“没配好”。"""
    from app.bot import cli
    from app.config import config as runtime_config

    target = tmp_path / "config.toml"
    target.write_text('[app]\nllm_provider = "moonshot"\n\n[bot]\n')
    monkeypatch.setattr(cli, "_config_path", lambda: target)
    monkeypatch.setattr(runtime_config, "app", {}, raising=False)
    monkeypatch.setattr(runtime_config, "bot", {}, raising=False)

    cli.set_config_values(["gemini_api_key=abc123"])
    assert 'llm_provider = "gemini"' in target.read_text()


def test_set_refuses_placeholder_keys(capsys):
    from app.bot import cli

    code = cli.main(["set", "gemini_api_key=YOUR-GEMINI-KEY"])
    assert code == 2
    assert "placeholder" in capsys.readouterr().out
