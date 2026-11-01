"""按周批量出片：一次做出 7 条，攒够一周的量再人工上传。

Path B 的工作方式：机器人负责写稿、配音、剪片、出文案；发布交给平台自己的免费
定时工具（Meta Business Suite、YouTube Studio、TikTok）。所以这里只做一件事——
把“一周的量”做成一个可以直接拖进那些工具的文件夹。

    python -m app.bot.cli week            # 7 条，默认用自动运营的选题
    python -m app.bot.cli week --count 3
"""

from __future__ import annotations

import csv
import os
import re
import shutil
import time
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from loguru import logger

from app.bot.models import ALL_PLATFORMS, JobState

# 每周的默认发布时段（当地时间）。人工排期时照抄即可，不必再想。
DEFAULT_SLOTS = ("09:00", "18:00")


def slugify(text: str, limit: int = 48) -> str:
    """把标题变成安全的文件名：小写、连字符、去掉奇怪字符。"""
    ascii_text = re.sub(r"[^\w\s-]", "", str(text or "").lower(), flags=re.UNICODE)
    ascii_text = re.sub(r"[\s_]+", "-", ascii_text).strip("-")
    return (ascii_text[:limit].strip("-") or "video")


def _storage_dir() -> str:
    from app.utils import utils

    return utils.storage_dir("bot", create=True)


def exports_dir() -> str:
    path = os.path.join(_storage_dir(), "exports")
    os.makedirs(path, exist_ok=True)
    return path


@dataclass
class BatchItem:
    index: int
    topic: str
    job_id: str = ""
    video: str = ""
    poster: str = ""
    captions: dict[str, Any] = field(default_factory=dict)
    ok: bool = False
    error: str = ""

    @property
    def stem(self) -> str:
        return f"{self.index:02d}-{slugify(self.topic, 40)}"


def _caption_text(caption: Any) -> str:
    """把 Caption 对象（或字典）拍平成可粘贴的一段文字。"""
    if isinstance(caption, dict):
        title = str(caption.get("title") or "")
        description = str(caption.get("description") or "")
        hashtags = caption.get("hashtags") or []
    else:
        title = str(getattr(caption, "title", "") or "")
        description = str(getattr(caption, "description", "") or "")
        hashtags = getattr(caption, "hashtags", []) or []

    tags = hashtags
    if isinstance(tags, str):
        tags = [tags]
    tag_line = " ".join(
        f"#{str(tag).lstrip('#').strip()}" for tag in tags if str(tag).strip()
    )
    # 没有 LLM 时标题和正文会一模一样，粘出来就是重复两遍，去掉一个。
    parts = [part for part in (title, description) if part.strip()]
    if len(parts) == 2 and parts[0].strip().lower() == parts[1].strip().lower():
        parts = parts[:1]
    text = "\n\n".join(parts).strip()
    if tag_line:
        text = f"{text}\n\n{tag_line}" if text else tag_line
    return text


def _wait(core: Any, job_id: str, state: JobState, timeout: float) -> Any:
    deadline = time.monotonic() + timeout
    job = None
    while time.monotonic() < deadline:
        job = core.job(job_id)
        if job is None or job.is_terminal:
            return job
        time.sleep(1.0)
    return job


def generate_batch(
    core: Any,
    topics: list[str],
    on_progress: Callable[[int, int, str], None] | None = None,
    timeout: float = 1800,
) -> list[BatchItem]:
    """逐条生成。串行是刻意的：一个渲染已经吃满 CPU，并行只会更慢。"""
    from app.bot.models import ChatMessage

    items: list[BatchItem] = []
    total = len(topics)
    for index, topic in enumerate(topics, start=1):
        if on_progress:
            on_progress(index, total, topic)

        replies = core.handle(
            ChatMessage(chat_id="week", text=topic, user_id="week")
        )
        job_id = next((reply.job_id for reply in replies if reply.job_id), "")
        if not job_id:
            reason = replies[0].text if replies else "could not start generation"
            items.append(
                BatchItem(index=index, topic=topic, ok=False, error=reason)
            )
            continue

        job = _wait(core, job_id, JobState.ready, timeout)
        if job is None or job.state is JobState.failed:
            items.append(
                BatchItem(
                    index=index,
                    topic=topic,
                    job_id=job_id,
                    ok=False,
                    error=getattr(job, "error", "") or "generation failed",
                )
            )
            continue

        items.append(
            BatchItem(
                index=index,
                topic=topic,
                job_id=job_id,
                video=job.video or "",
                poster=job.poster or "",
                captions=dict(job.captions or {}),
                ok=bool(job.video and os.path.isfile(job.video)),
                error="" if job.video else "no video file produced",
            )
        )
    return items


def _slots(count: int, per_day: int = 1, start: datetime | None = None) -> list[datetime]:
    start = start or datetime.now()
    slots: list[datetime] = []
    day = 0
    while len(slots) < count:
        for index in range(max(1, per_day)):
            if len(slots) >= count:
                break
            hour, _, minute = DEFAULT_SLOTS[index % len(DEFAULT_SLOTS)].partition(":")
            slots.append(
                (start + timedelta(days=day)).replace(
                    hour=int(hour), minute=int(minute), second=0, microsecond=0
                )
            )
        day += 1
    return slots


def write_batch(
    items: list[BatchItem],
    name: str = "",
    zip_it: bool = True,
) -> dict[str, Any]:
    """把成品整理成一个文件夹（外加 zip），附上排期表和说明。"""
    name = name or f"week-{datetime.now():%Y-%m-%d}"
    folder = os.path.join(exports_dir(), name)
    os.makedirs(folder, exist_ok=True)

    rows: list[dict[str, str]] = []
    ok_items = [item for item in items if item.ok]
    slots = _slots(len(ok_items))

    for item, slot in zip(ok_items, slots):
        stem = item.stem
        video_name = f"{stem}.mp4"
        shutil.copy2(item.video, os.path.join(folder, video_name))
        if item.poster and os.path.isfile(item.poster):
            shutil.copy2(item.poster, os.path.join(folder, f"{stem}.jpg"))

        # 一份纯文本，四种平台的文案按小标题分隔，方便一边看一边粘贴。
        blocks = [f"TOPIC: {item.topic}", ""]
        for platform in ALL_PLATFORMS:
            caption = item.captions.get(platform.value) or item.captions.get(
                platform.social_key
            )
            if not caption:
                continue
            blocks.append(f"=== {platform.label.upper()} ===")
            blocks.append(_caption_text(caption))
            blocks.append("")
        caption_path = os.path.join(folder, f"{stem}.txt")
        with open(caption_path, "w", encoding="utf-8") as handle:
            handle.write("\n".join(blocks).strip() + "\n")

        rows.append(
            {
                "slot": slot.strftime("%Y-%m-%d %H:%M"),
                "file": video_name,
                "captions": f"{stem}.txt",
                "topic": item.topic,
            }
        )

    with open(os.path.join(folder, "schedule.csv"), "w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=["slot", "file", "captions", "topic"]
        )
        writer.writeheader()
        writer.writerows(rows)

    _write_readme(folder, name, rows, failed=[i for i in items if not i.ok])

    archive = ""
    if zip_it:
        archive = os.path.join(exports_dir(), f"{name}.zip")
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
            for root, _dirs, files in os.walk(folder):
                for filename in files:
                    full = os.path.join(root, filename)
                    bundle.write(full, os.path.relpath(full, folder))

    return {
        "name": name,
        "folder": folder,
        "zip": archive,
        "count": len(ok_items),
        "failed": len(items) - len(ok_items),
        "rows": rows,
    }


def _write_readme(
    folder: str, name: str, rows: list[dict[str, str]], failed: list[BatchItem]
) -> None:
    lines = [
        f"{name} — a week of videos, ready to schedule",
        "=" * 60,
        "",
        f"{len(rows)} video(s), each with its own caption file.",
        "Upload them with the platforms' own free schedulers:",
        "",
        "  Instagram + Facebook   business.facebook.com/latest/scheduler",
        "  YouTube                studio.youtube.com  →  Create  →  Upload video",
        "  TikTok                 tiktok.com/upload  (has its own scheduler)",
        "",
        "Use the times in schedule.csv (your local time). Full walkthrough:",
        "SCHEDULING.md in the repo.",
        "",
        "Files",
        "-----",
    ]
    for row in rows:
        lines.append(f"{row['slot']}  {row['file']}")
        lines.append(f"                 caption: {row['captions']}")
    if failed:
        lines += ["", "These topics failed and are not in this folder:"]
        for item in failed:
            lines.append(f"  {item.index}. {item.topic} — {item.error}")
    lines += [
        "",
        "Tip: upload all of them in one sitting, then you are done for the week.",
        "",
    ]
    with open(os.path.join(folder, "README.txt"), "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines))


def batch_from_topics(
    core: Any,
    topics: list[str],
    name: str = "",
    on_progress: Callable[[int, int, str], None] | None = None,
) -> dict[str, Any]:
    """生成 + 整理 + 打包，一步到位。"""
    logger.info(f"weekly batch: {len(topics)} topic(s)")
    items = generate_batch(core, topics, on_progress=on_progress)
    result = write_batch(items, name=name or f"week-{datetime.now():%Y-%m-%d}")
    logger.info(
        f"weekly batch done: {result['count']} ok, {result['failed']} failed → {result['folder']}"
    )
    return result


def resolve_zip(name: str) -> str:
    """把请求的批次名解析成 exports 目录内的 zip 路径（防目录穿越）。"""
    safe = os.path.basename(str(name or "").strip())
    if not safe or not safe.endswith(".zip"):
        return ""
    path = os.path.join(exports_dir(), safe)
    if not os.path.isfile(path):
        return ""
    if os.path.dirname(os.path.realpath(path)) != os.path.realpath(exports_dir()):
        return ""
    return path
