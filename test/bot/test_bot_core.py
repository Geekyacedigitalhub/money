"""机器人内核的端到端测试（不需要任何第三方密钥）。

演示模式由本地 Pillow + FFmpeg 渲染，因此这些用例可以在 CI 里真实跑完整条
“主题 → 视频 → 预览 → 发布回执”的链路。
"""

from __future__ import annotations

import os
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.bot import captions as caption_service
from app.bot.config import BotSettings
from app.bot.core import BotCore
from app.bot.models import (
    ALL_PLATFORMS,
    ChatMessage,
    JobState,
    Platform,
    Session,
    parse_platform,
)
from app.bot.publisher import SimulatedPublisher


@pytest.fixture(scope="module")
def settings() -> BotSettings:
    return BotSettings(
        demo_mode=True,
        default_platforms=[p.value for p in ALL_PLATFORMS],
        max_concurrent_jobs=1,
    )


def make_core(settings: BotSettings, receipt_dir: str | None = None) -> BotCore:
    return BotCore(
        settings,
        publisher=SimulatedPublisher(receipt_dir),
        executor=ThreadPoolExecutor(max_workers=1),
    )


def wait_for(core: BotCore, chat_id: str, timeout: float = 120.0):
    """阻塞直到内核推送出异步回复。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        replies = core.drain_pending(chat_id)
        if replies:
            return replies
        time.sleep(0.15)
    raise AssertionError("timed out waiting for an async bot reply")


def wait_for_state(core: BotCore, chat_id: str, job_id: str, states, timeout: float = 120.0):
    """等待任务到达终态，同时持续清空推送队列。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        core.drain_pending(chat_id)
        job = core.job(job_id)
        if job is not None and job.state in states:
            return job
        time.sleep(0.15)
    raise AssertionError(f"job {job_id} never reached {states}")


# ---------------------------------------------------------------------------
# 基础
# ---------------------------------------------------------------------------


def test_platform_ids_map_to_each_service():
    # 内部标识、文案规格、Upload-Post 平台 ID 三者历史上拼写不同，映射必须稳定。
    assert Platform.instagram.social_key == "instagram_reels"
    assert Platform.instagram.upload_post_id == "instagram"
    assert Platform.youtube.social_key == "youtube_shorts"
    assert Platform.youtube.upload_post_id == "youtube"
    assert Platform.facebook.upload_post_id == "facebook"
    assert Platform.tiktok.upload_post_id == "tiktok"


@pytest.mark.parametrize(
    "value,expected",
    [
        ("IG", Platform.instagram),
        ("youtube_shorts", Platform.youtube),
        ("fb", Platform.facebook),
        (" reels ", Platform.instagram),
        ("nope", None),
        ("", None),
    ],
)
def test_parse_platform(value, expected):
    assert parse_platform(value) is expected


def test_session_toggle_keeps_stable_order():
    session = Session(chat_id="c", platforms=[])
    assert session.toggle(Platform.youtube) is True
    assert session.toggle(Platform.tiktok) is True
    # 顺序固定为 ALL_PLATFORMS 的声明顺序，而不是点击顺序。
    assert session.platforms == ["tiktok", "youtube"]
    assert session.toggle(Platform.youtube) is False
    assert session.platforms == ["tiktok"]


def test_allow_list_blocks_strangers(settings):
    locked = BotSettings(demo_mode=True, allowed_users=("42",))
    core = make_core(locked)
    replies = core.handle(ChatMessage(chat_id="c1", text="hello", user_id="7"))
    assert "restricted" in replies[0].text.lower()

    allowed = core.handle(ChatMessage(chat_id="c1", text="/help", user_id="42"))
    assert "MoneyPrinterTurbo Bot" in allowed[0].text


def test_help_lists_all_commands(settings):
    core = make_core(settings)
    text = core.handle(ChatMessage(chat_id="c", text="/help", user_id="u"))[0].text
    for command in ("/platforms", "/status", "/preview", "/caption", "/cancel", "/how"):
        assert command in text


def test_help_keyboard_has_all_four_platforms(settings):
    core = make_core(settings)
    reply = core.handle(ChatMessage(chat_id="c", text="/help", user_id="u"))[0]
    flat = [button.data for row in reply.buttons for button in row]
    for platform in ALL_PLATFORMS:
        assert f"pl:{platform.value}" in flat


def test_platform_toggle_callback(settings):
    core = make_core(settings)
    core.handle(ChatMessage(chat_id="c", text="/help", user_id="u"))

    core.handle(ChatMessage(chat_id="c", callback="pl:tiktok", user_id="u"))
    assert "tiktok" not in core.session("c").platforms

    core.handle(ChatMessage(chat_id="c", callback="pl:tiktok", user_id="u"))
    assert "tiktok" in core.session("c").platforms


# ---------------------------------------------------------------------------
# 文案
# ---------------------------------------------------------------------------


def test_heuristic_captions_respect_platform_limits():
    for platform, tag_limit in (
        (Platform.youtube, 3),
        (Platform.tiktok, 5),
        (Platform.instagram, 8),
    ):
        caption = caption_service._heuristic_caption(
            "How inflation is eating the naira in Nigeria right now",
            "Inflation is eating the naira. Prices doubled in two years.",
            platform,
        )
        assert len(caption.hashtags) <= tag_limit
        assert caption.description


def test_caption_adapt_clips_to_platform_limits():
    long_text = "word " * 400
    caption = caption_service.Caption(title=long_text, description=long_text, hashtags=["#a", "#b", "#c", "#d"])
    adapted = caption_service.adapt(caption, Platform.youtube)
    assert len(adapted.title) <= 100
    assert len(adapted.hashtags) <= 3


def test_caption_full_text_includes_hashtags():
    caption = caption_service.Caption(
        title="Title", description="Body", hashtags=["#One", "#Two"]
    )
    text = caption.full_text()
    assert "Body" in text and "#One #Two" in text


# ---------------------------------------------------------------------------
# 演示模式端到端
# ---------------------------------------------------------------------------


def test_demo_generation_produces_a_real_video(tmp_path, settings):
    core = make_core(settings, receipt_dir=str(tmp_path))
    replies = core.handle(
        ChatMessage(chat_id="demo", text="How inflation affects the naira", user_id="u")
    )
    job_id = replies[0].job_id
    assert job_id

    preview = wait_for(core, "demo")[0]
    job = core.job(job_id)
    assert job is not None
    assert job.state is JobState.ready
    assert job.video and os.path.isfile(job.video)
    assert os.path.getsize(job.video) > 1000
    assert job.simulated is True
    # 四个平台都应该生成了各自的文案
    assert set(job.captions) == {p.value for p in ALL_PLATFORMS}
    assert preview.video == job.video
    assert preview.buttons


def test_simulated_publish_never_touches_the_network(tmp_path, settings):
    core = make_core(settings, receipt_dir=str(tmp_path))
    core.handle(ChatMessage(chat_id="pub", text="A test topic", user_id="u"))
    wait_for(core, "pub")
    job = core.jobs()[0]

    core.handle(ChatMessage(chat_id="pub", callback=f"pub:{job.id}", user_id="u"))
    job = wait_for_state(core, "pub", job.id, {JobState.published, JobState.failed})

    assert job.state is JobState.published
    assert job.simulated is True
    assert set(job.results) == {p.value for p in ALL_PLATFORMS}
    receipts = list(tmp_path.glob("receipt-*.json"))
    assert len(receipts) == 1


def test_attachment_flow_uses_uploaded_file(tmp_path, settings):
    video = tmp_path / "my_promo.mp4"
    video.write_bytes(b"\x00" * 4096)

    core = make_core(settings, receipt_dir=str(tmp_path / "receipts"))
    replies = core.handle(
        ChatMessage(chat_id="up", attachment=str(video), user_id="u")
    )
    assert replies[0].video == str(video)
    job = core.jobs()[0]
    assert job.state is JobState.ready
    assert "my promo" in job.topic.lower()


def test_attachment_rejects_oversized_file(tmp_path):
    tiny_limit = BotSettings(demo_mode=True, max_upload_mb=1)
    big = tmp_path / "big.mp4"
    big.write_bytes(b"\x00" * (2 * 1024 * 1024))
    core = make_core(tiny_limit)
    replies = core.handle(ChatMessage(chat_id="big", attachment=str(big), user_id="u"))
    assert "over the" in replies[0].text


def test_cancel_marks_job_failed(settings):
    core = make_core(settings)
    replies = core.handle(ChatMessage(chat_id="cx", text="some topic", user_id="u"))
    job_id = replies[0].job_id
    cancel_reply = core.handle(ChatMessage(chat_id="cx", text="/cancel", user_id="u"))[0]
    assert "Cancelled" in cancel_reply.text
    job = core.job(job_id)
    assert job.state is JobState.failed


def test_caption_override(settings):
    core = make_core(settings)
    core.handle(ChatMessage(chat_id="cap", text="topic", user_id="u"))
    wait_for(core, "cap")
    job = core.jobs()[0]
    core.handle(ChatMessage(chat_id="cap", text="/caption My custom caption", user_id="u"))
    job = core.job(job.id)
    for caption in job.captions.values():
        assert "My custom caption" in caption.description


def test_discard_marks_job(settings):
    core = make_core(settings)
    core.handle(ChatMessage(chat_id="d", text="topic", user_id="u"))
    wait_for(core, "d")
    job = core.jobs()[0]
    core.handle(ChatMessage(chat_id="d", callback=f"drop:{job.id}", user_id="u"))
    assert core.job(job.id).state is JobState.failed


def test_unknown_callback_is_harmless(settings):
    core = make_core(settings)
    replies = core.handle(ChatMessage(chat_id="x", callback="pub:does-not-exist", user_id="u"))
    assert replies and "no longer exists" in replies[0].text


def test_delivery_guide_mentions_tiktok_minute_rule():
    from app.bot import delivery

    text = delivery.format_guides()
    assert "TikTok" in text
    assert "Nigeria" in text
