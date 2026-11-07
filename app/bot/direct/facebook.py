"""Facebook Page video posts through the Graph API — free, and Nigeria-eligible
for Content Monetization.

Auth is a Page access token, which Meta hands out without any OAuth dance: the
Graph API Explorer generates one, and a token from a System User in Business
Suite does not expire. That is why this platform is the least work of the four.
"""

from __future__ import annotations

import os

import requests
from loguru import logger

from app.bot.direct import tokens
from app.bot.direct.base import DirectResult, _caption_text
from app.bot.models import Platform

PLATFORM = "facebook"
GRAPH_VIDEO = "https://graph-video.facebook.com/v21.0"
GRAPH = "https://graph.facebook.com/v21.0"
TIMEOUT = 900


def settings() -> dict:
    from app.config import config as runtime_config

    section = getattr(runtime_config, "direct", {}) or {}
    return {key: section.get(key) for key in section}


def page_ready() -> tuple[str, str]:
    """Page id and token, or a reason why not."""
    record = tokens.get(PLATFORM)
    page_id = str(record.get("page_id") or "").strip()
    token = str(record.get("access_token") or "").strip()
    if not page_id:
        return "", "no Facebook Page id — run: python -m app.bot.cli connect facebook"
    if not token:
        return "", "no Facebook Page token — run: python -m app.bot.cli connect facebook"
    return page_id, token


def ready() -> tuple[bool, str]:
    page_id, token = page_ready()
    if not page_id or not token:
        return False, token or page_id
    return True, ""


def verify(page_id: str, token: str) -> str:
    """Ask Meta for the Page name so a pasted token cannot look 'connected'."""
    response = requests.get(
        f"{GRAPH}/{page_id}",
        params={"fields": "name", "access_token": token},
        timeout=30,
    )
    if response.status_code >= 400:
        raise RuntimeError(_explain(response))
    body = response.json() if response.content else {}
    name = str((body or {}).get("name") or "")
    if not name:
        raise RuntimeError("Meta answered but returned no Page name")
    return name


def connect(page_id: str, token: str) -> dict:
    page_id, token = page_id.strip(), token.strip()
    if not page_id.isdigit():
        raise RuntimeError(
            "the Page id is all digits — open your Page, About, Page transparency, "
            "and copy the Page ID"
        )
    name = verify(page_id, token)
    return tokens.put(PLATFORM, page_id=page_id, access_token=token, page_name=name)


def publish(video_path: str, caption, **options) -> DirectResult:
    if not os.path.isfile(video_path):
        return DirectResult(False, f"video not found: {video_path}")

    page_id, token = page_ready()
    if not page_id or not token:
        return DirectResult(False, token or page_id)

    text = _caption_text(caption)
    data = {
        "access_token": token,
        "description": text,
        "published": "true",
    }

    try:
        with open(video_path, "rb") as handle:
            response = requests.post(
                f"{GRAPH_VIDEO}/{page_id}/videos",
                data=data,
                files={"source": (os.path.basename(video_path), handle, "video/mp4")},
                timeout=TIMEOUT,
            )
    except OSError as exc:
        return DirectResult(False, f"cannot read the video file: {exc}")

    if response.status_code >= 400:
        return DirectResult(False, _explain(response))

    body = response.json() if response.content else {}
    video_id = str((body or {}).get("id") or "")
    if not video_id:
        return DirectResult(False, "Meta accepted the upload but returned no video id")

    logger.info(f"facebook upload ok: {video_id}")
    return DirectResult(
        True,
        f"posted to the Page ({video_id})",
        url=f"https://www.facebook.com/{video_id}",
    )


def _explain(response) -> str:
    try:
        body = response.json()
    except ValueError:
        return f"HTTP {response.status_code}: {response.text[:300]}"

    error = body.get("error") if isinstance(body, dict) else None
    if isinstance(error, dict):
        message = str(error.get("message") or "")
        code = error.get("code")
        if code == 190:
            return (
                "the Facebook token is invalid or expired — run: "
                "python -m app.bot.cli connect facebook"
            )
        if code == 200:
            return "the token lacks pages_manage_posts — make a Page token again"
        return message or f"HTTP {response.status_code}"
    return f"HTTP {response.status_code}: {response.text[:300]}"


class FacebookTarget:
    platform = Platform.facebook

    def ready(self) -> tuple[bool, str]:
        return ready()

    def publish(self, video_path: str, caption) -> DirectResult:
        return publish(video_path, caption)
