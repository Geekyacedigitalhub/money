"""演示模式渲染。

没有配置任何模型 / 素材 / TTS 密钥时，机器人仍然需要跑通“输入主题 → 看到视频
→ 选择平台 → 发布”的完整链路，否则用户无法在购买密钥之前验证部署是否正确。

这里用 Pillow 画一张竖版海报，再用 FFmpeg 加缓慢推镜，产出一段真实可播放的
MP4。它明确标注 DEMO，避免被误当成正式成片发布。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import textwrap

from loguru import logger

WIDTH = 1080
HEIGHT = 1920
DURATION_SECONDS = 6

_FONT_CANDIDATES = (
    "resource/fonts/STHeitiMedium.ttc",
    "resource/fonts/MicrosoftYaHeiBold.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
)


def _root_dir() -> str:
    from app.utils import utils

    return utils.root_dir()


def _load_font(size: int):
    from PIL import ImageFont

    for candidate in _FONT_CANDIDATES:
        path = candidate if os.path.isabs(candidate) else os.path.join(_root_dir(), candidate)
        if os.path.isfile(path):
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    return ImageFont.load_default()


def _ffmpeg_binary() -> str | None:
    """优先使用项目配置的 FFmpeg，其次 PATH，最后 imageio-ffmpeg 自带副本。"""
    configured = os.environ.get("IMAGEIO_FFMPEG_EXE", "").strip()
    if configured and os.path.isfile(configured):
        return configured
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg

        bundled = imageio_ffmpeg.get_ffmpeg_exe()
        if bundled and os.path.isfile(bundled):
            return bundled
    except Exception:  # noqa: BLE001 - 可选依赖
        return None


def _vertical_gradient():
    from PIL import Image

    image = Image.new("RGB", (WIDTH, HEIGHT), "#0b1220")
    top = (15, 23, 42)
    bottom = (30, 64, 110)
    pixels = image.load()
    for y in range(HEIGHT):
        ratio = y / max(1, HEIGHT - 1)
        color = tuple(
            int(top[index] + (bottom[index] - top[index]) * ratio) for index in range(3)
        )
        for x in range(WIDTH):
            pixels[x, y] = color
    return image


def render_poster(topic: str, output_path: str, subtitle: str = "DEMO PREVIEW") -> str:
    """画一张 1080x1920 的竖版封面。"""
    from PIL import ImageDraw

    image = _vertical_gradient()
    draw = ImageDraw.Draw(image)

    # 顶部品牌条
    draw.rectangle([0, 0, WIDTH, 12], fill=(56, 189, 248))

    badge_font = _load_font(40)
    badge_text = subtitle.upper()
    badge_box = draw.textbbox((0, 0), badge_text, font=badge_font)
    badge_w = badge_box[2] - badge_box[0]
    draw.rounded_rectangle(
        [(WIDTH - badge_w) // 2 - 36, 150, (WIDTH + badge_w) // 2 + 36, 150 + 96],
        radius=48,
        fill=(250, 204, 21),
    )
    draw.text(
        ((WIDTH - badge_w) // 2, 150 + 22),
        badge_text,
        font=badge_font,
        fill=(15, 23, 42),
    )

    # 标题：按宽度折行，字号随长度收缩，保证不溢出。
    headline = " ".join(str(topic or "Your video topic").split())
    size = 96
    lines: list[str] = []
    while size >= 48:
        font = _load_font(size)
        lines = textwrap.wrap(headline, width=max(12, int(WIDTH / (size * 0.58))))
        if len(lines) <= 6:
            break
        size -= 8
    font = _load_font(size)
    line_height = int(size * 1.32)
    block_height = line_height * len(lines)
    y = (HEIGHT - block_height) // 2
    for line in lines[:8]:
        box = draw.textbbox((0, 0), line, font=font)
        draw.text(
            ((WIDTH - (box[2] - box[0])) // 2, y),
            line,
            font=font,
            fill=(248, 250, 252),
        )
        y += line_height

    # 底部提示
    footer_font = _load_font(38)
    footer = "MoneyPrinterTurbo Bot"
    box = draw.textbbox((0, 0), footer, font=footer_font)
    draw.text(
        ((WIDTH - (box[2] - box[0])) // 2, HEIGHT - 190),
        footer,
        font=footer_font,
        fill=(148, 163, 184),
    )

    image.save(output_path, "PNG")
    return output_path


def render_demo_video(topic: str, output_dir: str, subtitle: str = "DEMO PREVIEW") -> tuple[str | None, str | None]:
    """渲染演示成片，返回 ``(视频路径, 封面路径)``。

    缺少 Pillow 时只产出封面（或什么都不产出），调用方需要能接受 ``None``。
    """
    os.makedirs(output_dir, exist_ok=True)
    poster_path = os.path.join(output_dir, "poster.png")
    try:
        render_poster(topic, poster_path, subtitle)
    except Exception as exc:  # noqa: BLE001 - 演示模式不应让任务失败
        logger.warning(f"demo poster rendering failed: {exc}")
        return None, None

    ffmpeg = _ffmpeg_binary()
    if not ffmpeg:
        logger.warning("demo video skipped: ffmpeg not found")
        return None, poster_path

    video_path = os.path.join(output_dir, "final-1.mp4")
    frames = DURATION_SECONDS * 30
    filters = (
        f"zoompan=z='min(zoom+0.0006,1.10)'"
        f":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
        f":d={frames}:s={WIDTH}x{HEIGHT}:fps=30,"
        f"fade=t=in:st=0:d=0.5,fade=t=out:st={DURATION_SECONDS - 0.6}:d=0.6,"
        "format=yuv420p"
    )
    command = [
        ffmpeg, "-y", "-loop", "1", "-i", poster_path,
        "-t", str(DURATION_SECONDS),
        "-vf", filters,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
        "-pix_fmt", "yuv420p", "-movflags", "+faststart",
        video_path,
    ]
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, timeout=180, check=False
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning(f"demo video encoding failed: {exc}")
        return None, poster_path

    if result.returncode != 0 or not os.path.isfile(video_path):
        logger.warning(
            "demo video encoding failed: " + (result.stderr or "").strip()[-400:]
        )
        return None, poster_path
    return video_path, poster_path
