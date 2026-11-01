"""网页聊天适配器。

把内核的 ``Reply`` 暴露成一个很小的 JSON API，并提供一个手机友好的聊天界面。
之所以先做网页而不是 Telegram：不需要任何平台账号、审核或公网 webhook，部署完
就能用。

路由前缀是 ``/bot``（页面）与 ``/api/bot``（接口），并且独立于
``/api/v1``，因此不会影响原有 API 的鉴权与行为。
"""

from __future__ import annotations

import os
import secrets
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from loguru import logger

from app.bot import delivery
from app.bot import publisher as publisher_service
from app.bot.core import BotCore, build_core
from app.bot.models import ALL_PLATFORMS, Button, ChatMessage, Reply

_STATIC_DIR = Path(__file__).resolve().parent / "static"
_UPLOAD_SUBDIR = "uploads"

router = APIRouter()


def _core() -> BotCore:
    return build_core()


def _storage_root() -> str:
    from app.utils import utils

    return os.path.realpath(utils.storage_dir(create=True))


def _inside_storage(path: str) -> bool:
    """只允许把 storage 目录内的文件交给浏览器，阻断路径穿越。"""
    try:
        real = os.path.realpath(path)
    except OSError:
        return False
    root = _storage_root()
    return real == root or real.startswith(root + os.sep)


def _serialize_button(button: Button) -> dict[str, str]:
    return {"label": button.label, "data": button.data}


def _serialize_reply(reply: Reply) -> dict[str, Any]:
    # 所有带视频的回复都会携带 job_id（预览、状态、发布结果），因此媒体地址
    # 只按任务 ID 暴露，避免把任意服务器路径交给浏览器。
    video_url = f"/api/bot/media/{reply.job_id}" if reply.video and reply.job_id else None
    poster_url = (
        f"/api/bot/poster/{reply.job_id}"
        if reply.video and reply.poster and reply.job_id
        else None
    )
    return {
        "text": reply.text,
        "video_url": video_url,
        "poster_url": poster_url,
        "buttons": [[_serialize_button(item) for item in row] for row in reply.buttons],
        "job_id": reply.job_id,
    }


def _new_chat_id() -> str:
    return "web-" + secrets.token_hex(8)


# ----------------------------------------------------------------------
# 页面
# ----------------------------------------------------------------------


@router.get("/bot", response_class=HTMLResponse, include_in_schema=False)
def bot_page() -> HTMLResponse:
    index = _STATIC_DIR / "index.html"
    if not index.is_file():
        raise HTTPException(status_code=500, detail="bot UI assets are missing")
    return HTMLResponse(index.read_text(encoding="utf-8"))


@router.get("/bot/assets/{name}", include_in_schema=False)
def bot_asset(name: str) -> FileResponse:
    # 白名单式匹配，避免 ../ 穿越到任意文件。
    allowed = {"app.css", "app.js"}
    if name not in allowed:
        raise HTTPException(status_code=404, detail="not found")
    path = _STATIC_DIR / name
    if not path.is_file():
        raise HTTPException(status_code=404, detail="not found")
    return FileResponse(path)


# ----------------------------------------------------------------------
# 接口
# ----------------------------------------------------------------------


@router.get("/api/bot/health")
def bot_health() -> dict[str, Any]:
    core = _core()
    ready, reason = publisher_service.publish_account_ready()
    return {
        "ok": True,
        "bot_name": core.settings.display_name,
        "owner_name": core.settings.greeting_name,
        "demo_mode": bool(core.settings.demo_mode or core.settings.is_demo),
        "publishing_live": ready,
        "publishing_reason": reason,
        "publisher": core.publisher_name,
        "platforms": [item.value for item in ALL_PLATFORMS],
    }


@router.get("/api/bot/session")
def bot_session(chat_id: str = "") -> dict[str, Any]:
    session = _core().session(chat_id or _new_chat_id())
    return {
        "chat_id": session.chat_id,
        "platforms": session.platforms,
        "platform_labels": [
            {"value": item.value, "label": item.label, "emoji": item.emoji}
            for item in ALL_PLATFORMS
        ],
    }


@router.get("/api/bot/guides")
def platform_guides() -> dict[str, Any]:
    rows = [
        {
            "platform": guide.platform.value,
            "label": guide.platform.label,
            "max_seconds": guide.max_seconds,
            "aspect": guide.aspect,
            "account": guide.account_requirement,
            "monetization": guide.monetization,
            "gotcha": guide.gotcha,
        }
        for guide in delivery.GUIDES.values()
    ]
    return {"guides": rows}


@router.post("/api/bot/message")
def bot_message(payload: dict[str, Any]) -> dict[str, Any]:
    chat_id = str(payload.get("chat_id") or "").strip() or _new_chat_id()
    text = str(payload.get("text") or "")
    replies = _core().handle(ChatMessage(chat_id=chat_id, text=text, user_id=chat_id))
    return {"chat_id": chat_id, "replies": [_serialize_reply(item) for item in replies]}


@router.post("/api/bot/callback")
def bot_callback(payload: dict[str, Any]) -> dict[str, Any]:
    chat_id = str(payload.get("chat_id") or "").strip()
    data = str(payload.get("data") or "").strip()
    if not chat_id or not data:
        raise HTTPException(status_code=400, detail="chat_id and data are required")
    replies = _core().handle(ChatMessage(chat_id=chat_id, callback=data, user_id=chat_id))
    return {"chat_id": chat_id, "replies": [_serialize_reply(item) for item in replies]}


@router.get("/api/bot/feed")
def bot_feed(chat_id: str) -> dict[str, Any]:
    """异步工作的产出（生成完成、发布结果）。前端轮询这个端点。"""
    if not chat_id:
        raise HTTPException(status_code=400, detail="chat_id is required")
    replies = _core().drain_pending(chat_id)
    return {"replies": [_serialize_reply(item) for item in replies]}


@router.post("/api/bot/upload")
async def bot_upload(
    file: Annotated[UploadFile, File()],
    chat_id: str = "",
) -> dict[str, Any]:
    """直接转发一条已经做好的 MP4 去发布。"""
    if not chat_id:
        raise HTTPException(status_code=400, detail="chat_id is required")
    core = _core()
    limit = max(1, core.settings.max_upload_mb) * 1024 * 1024

    upload_dir = os.path.join(_storage_root(), "bot", _UPLOAD_SUBDIR)
    os.makedirs(upload_dir, exist_ok=True)

    suffix = os.path.splitext(file.filename or "")[1].lower() or ".mp4"
    if suffix not in {".mp4", ".mov", ".m4v", ".webm", ".mkv"}:
        raise HTTPException(status_code=400, detail=f"unsupported file type: {suffix}")

    target = os.path.join(upload_dir, f"{secrets.token_hex(8)}{suffix}")
    written = 0
    try:
        # 上传是顺序写入；用同步 open 即可，避免为一次磁盘写入引入额外依赖。
        with open(target, "wb") as handle:  # noqa: ASYNC230
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                written += len(chunk)
                if written > limit:
                    raise HTTPException(status_code=413, detail="file too large")
                handle.write(chunk)
    except HTTPException:
        try:
            os.remove(target)
        except OSError:
            pass
        raise
    finally:
        await file.close()

    replies = core.handle(
        ChatMessage(chat_id=chat_id, attachment=target, user_id=chat_id)
    )
    return {"chat_id": chat_id, "replies": [_serialize_reply(item) for item in replies]}


@router.get("/api/bot/jobs/{job_id}")
def bot_job(job_id: str) -> dict[str, Any]:
    job = _core().job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    payload = job.to_dict()
    payload["video_url"] = f"/api/bot/media/{job.id}" if job.video else None
    return payload


@router.get("/api/bot/media/{job_id}")
def bot_media(job_id: str) -> FileResponse:
    job = _core().job(job_id)
    if job is None or not job.video or not os.path.isfile(job.video):
        raise HTTPException(status_code=404, detail="video not found")
    if not _inside_storage(job.video):
        logger.warning(f"refusing to serve file outside storage: {job.video}")
        raise HTTPException(status_code=403, detail="forbidden")
    return FileResponse(job.video, media_type="video/mp4", filename=os.path.basename(job.video))



@router.get("/api/bot/poster/{job_id}")
def bot_poster(job_id: str) -> FileResponse:
    job = _core().job(job_id)
    if job is None or not job.poster or not os.path.isfile(job.poster):
        raise HTTPException(status_code=404, detail="poster not found")
    if not _inside_storage(job.poster):
        raise HTTPException(status_code=403, detail="forbidden")
    return FileResponse(job.poster, media_type="image/png")
