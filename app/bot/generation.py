"""生成层：把“主题”变成一条可发布的成品视频。

两种实现：

* ``demo``  —— 不调用任何付费服务，用 Pillow + FFmpeg 渲染一段带 DEMO 标记的
  真实 MP4。用来在没有密钥的机器上验证整条链路，也让部署者第一次打开机器人
  就能看到完整流程。
* ``real``  —— 走 MoneyPrinterTurbo 自己的流水线（LLM 脚本 → TTS 配音 → 字幕
  → 素材 → 合成），与 WebUI / API / CLI 完全同一套逻辑，因此配置一份
  config.toml 就能同时被三者复用。
"""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from loguru import logger

from app.bot import demo
from app.bot.config import BotSettings

ProgressCallback = Callable[[int, str], None]


class GenerationError(RuntimeError):
    """生成失败，消息会直接展示给用户。"""


@dataclass
class GenerationResult:
    video: str
    poster: str | None = None
    script: str = ""
    task_id: str = ""
    simulated: bool = False
    warnings: list[str] = field(default_factory=list)


LOCAL_SOURCE = "local"
_VIDEO_SUFFIXES = (".mp4", ".mov", ".webm", ".mkv", ".m4v")


def _local_videos_dir() -> str:
    from app.utils import utils

    return utils.storage_dir("local_videos", create=True)


def _local_materials(source: str) -> list | None:
    """``video_source = "local"`` 时，把素材目录里的片段交给渲染器。

    这是唯一一种不需要任何素材 API Key 的方式：用户自己把下载好的片段放进来
    （Pexels / Pixabay / Coverr 网站都能直接下载，无需申请 Key）。
    """
    if str(source or "").strip().lower() != LOCAL_SOURCE:
        return None

    import os

    from app.models.schema import MaterialInfo

    directory = _local_videos_dir()
    try:
        names = sorted(
            name
            for name in os.listdir(directory)
            if name.lower().endswith(_VIDEO_SUFFIXES)
            and os.path.isfile(os.path.join(directory, name))
        )
    except OSError as exc:
        logger.warning(f"local materials unreadable: {exc}")
        return []

    return [MaterialInfo(provider="local", url=name) for name in names]


def _aspect_value(settings: BotSettings) -> str:
    raw = str(settings.video_aspect or "9:16").strip()
    aliases = {
        "portrait": "9:16",
        "vertical": "9:16",
        "9:16": "9:16",
        "landscape": "16:9",
        "horizontal": "16:9",
        "16:9": "16:9",
        "square": "1:1",
        "1:1": "1:1",
    }
    return aliases.get(raw.lower(), "9:16")


def _job_output_dir(job_id: str) -> str:
    from app.utils import utils

    path = os.path.join(utils.storage_dir("bot", create=True), job_id)
    os.makedirs(path, exist_ok=True)
    return path


def generate(
    job_id: str,
    topic: str,
    script: str = "",
    settings: BotSettings | None = None,
    on_progress: ProgressCallback | None = None,
    cancel: threading.Event | None = None,
) -> GenerationResult:
    """同步执行一次生成。调用方负责放进线程池。"""
    settings = settings or BotSettings()
    report = on_progress or (lambda percent, message: None)
    output_dir = _job_output_dir(job_id)

    def cancelled() -> bool:
        return bool(cancel and cancel.is_set())

    if settings.demo_mode or settings.is_demo:
        report(10, "Rendering demo preview")
        video, poster = demo.render_demo_video(topic or "Untitled topic", output_dir)
        if cancelled():
            raise GenerationError("cancelled")
        if not video:
            raise GenerationError(
                "Demo rendering needs Pillow and FFmpeg. Install them, or set "
                "[bot] demo_mode = false once your API keys are configured."
            )
        report(100, "Demo preview ready")
        return GenerationResult(
            video=video,
            poster=poster,
            # 演示模式没有真实脚本。留空让文案走“紧贴主题”的启发式生成，
            # 而不是把 “Demo preview for: …” 当成正文发出去。
            script=script or "",
            simulated=True,
            warnings=[
                (
                    "This is a DEMO video rendered locally. Set [bot] demo_mode = "
                    "false and configure your LLM / TTS / stock-footage keys for "
                    "real output."
                )
            ],
        )

    return _generate_real(job_id, topic, script, settings, report, cancelled)


def _generate_real(
    job_id: str,
    topic: str,
    script: str,
    settings: BotSettings,
    report: ProgressCallback,
    cancelled: Callable[[], bool],
) -> GenerationResult:
    # 延迟导入：这些模块会把 moviepy / openai / faster-whisper 一并载入，
    # 演示模式和纯配置类命令不应该付这个启动成本。
    try:
        from app.models import const
        from app.models.schema import VideoAspect, VideoParams
        from app.services import state as sm
        from app.services import task as tm
        from app.utils import utils
    except Exception as exc:
        raise GenerationError(
            f"Generation dependencies are missing ({exc}). Run 'uv sync --frozen' "
            "or use demo mode."
        ) from exc

    materials = _local_materials(settings.video_source)
    if settings.video_source == "local" and not materials:
        raise GenerationError(
            "video_source = \"local\" but there are no clips in "
            f"{_local_videos_dir()} — drop a few .mp4 files there, then retry "
            "(see WINDOWS.md, 'no stock-footage key')."
        )

    try:
        params = VideoParams(
            video_subject=topic,
            video_script=script or "",
            video_aspect=VideoAspect(_aspect_value(settings)),
            video_source=settings.video_source or "pexels",
            video_materials=materials,
            voice_name=settings.voice_name or "en-US-AriaNeural-Female",
            video_language=settings.video_language or "",
            paragraph_number=settings.paragraph_number,
            subtitle_enabled=settings.subtitle_enabled,
        )
    except Exception as exc:
        raise GenerationError(f"invalid generation settings: {exc}") from exc

    task_id = utils.get_uuid()
    sm.state.update_task(task_id, state=const.TASK_STATE_PROCESSING, progress=0)
    report(5, "Starting generation")

    done = threading.Event()
    failure: list[BaseException] = []

    def runner() -> None:
        try:
            tm.start(task_id, params, stop_at="video")
        except BaseException as exc:  # noqa: BLE001 - 需要把异常带回轮询线程
            failure.append(exc)
            logger.exception(f"bot generation crashed: {exc}")
        finally:
            done.set()

    thread = threading.Thread(
        target=runner, name=f"mpt-bot-{task_id[:8]}", daemon=True
    )
    thread.start()

    deadline = time.monotonic() + max(60, settings.job_timeout_seconds)
    last_progress = -1
    last_message = ""
    while not done.wait(timeout=1.5):
        if cancelled():
            logger.info(f"bot job {job_id} cancelled while generating")
            raise GenerationError("cancelled")
        if time.monotonic() > deadline:
            raise GenerationError(
                f"generation timed out after {settings.job_timeout_seconds}s"
            )
        snapshot = sm.state.get_task(task_id) or {}
        progress = int(snapshot.get("progress", 0) or 0)
        message = str(snapshot.get("progress_message") or snapshot.get("stage") or "")
        if progress != last_progress or message != last_message:
            last_progress, last_message = progress, message
            report(max(5, min(99, progress)), message or "Generating")

    if failure:
        raise GenerationError(str(failure[0]) or "generation failed")

    snapshot = sm.state.get_task(task_id) or {}
    if int(snapshot.get("state", 0) or 0) == const.TASK_STATE_FAILED:
        raise GenerationError(
            str(snapshot.get("error") or "generation failed").strip()
        )

    videos = snapshot.get("videos") or []
    if not videos:
        raise GenerationError("generation finished without producing a video")
    video_path = str(videos[0])
    if not os.path.isfile(video_path):
        raise GenerationError(f"generated file is missing: {video_path}")

    report(100, "Generation complete")
    warnings = snapshot.get("warnings") or []
    return GenerationResult(
        video=video_path,
        script=str(snapshot.get("script") or script or ""),
        task_id=task_id,
        simulated=False,
        warnings=[
            str(item.get("message") or item)
            for item in warnings
            if isinstance(item, (dict, str))
        ],
    )
