"""YouTube uploads through the Data API v3 — free, 6 uploads/day by default.

Auth is OAuth2 with a refresh token, because Google does not hand out permanent
keys for uploads. The consent step runs once, on your own machine, and the
refresh token is stored on the server; after that the bot uploads on its own
without ever asking again.

Quota: every upload costs 1600 units of the default 10,000/day, so 6 videos a
day is the ceiling. That is twice what the autopilot posts.
"""

from __future__ import annotations

import json
import os
import time

import requests
from loguru import logger

from app.bot.direct import tokens
from app.bot.direct.base import DirectResult
from app.bot.models import Platform

PLATFORM = "youtube"
SCOPE = "https://www.googleapis.com/auth/youtube.upload"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos"
REDIRECT_URI = "http://localhost:8765/"

TIMEOUT = 60
CHUNK = 1024 * 1024


def client_credentials() -> tuple[str, str]:
    from app.config import config as runtime_config

    section = getattr(runtime_config, "direct", {}) or {}
    return (
        str(section.get("youtube_client_id", "") or "").strip(),
        str(section.get("youtube_client_secret", "") or "").strip(),
    )


def authorize_url(client_id: str, redirect_uri: str = REDIRECT_URI) -> str:
    from urllib.parse import urlencode

    query = urlencode(
        {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": SCOPE,
            "access_type": "offline",
            # Without this Google returns a refresh token only the very first
            # time, which turns a re-connect into a support thread.
            "prompt": "consent",
        }
    )
    return f"{AUTH_URL}?{query}"


def _post_token(payload: dict) -> dict:
    response = requests.post(TOKEN_URL, data=payload, timeout=TIMEOUT)
    body = response.json() if response.content else {}
    if response.status_code >= 400:
        detail = body.get("error_description") or body.get("error") or response.text
        raise RuntimeError(f"Google rejected the token request: {detail}")
    return body


def exchange_code(code: str, redirect_uri: str = REDIRECT_URI) -> dict:
    client_id, client_secret = client_credentials()
    if not client_id or not client_secret:
        raise RuntimeError(
            "set direct.youtube_client_id and direct.youtube_client_secret first"
        )
    body = _post_token(
        {
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code.strip(),
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri,
        }
    )
    if not body.get("refresh_token"):
        raise RuntimeError(
            "Google returned no refresh token — remove the app's access at "
            "myaccount.google.com/permissions and connect again"
        )
    return tokens.put(
        PLATFORM,
        refresh_token=body["refresh_token"],
        access_token=body.get("access_token", ""),
        expires_at=int(time.time()) + int(body.get("expires_in", 3600)),
    )


def access_token(force: bool = False) -> str:
    record = tokens.get(PLATFORM)
    if not record.get("refresh_token"):
        raise RuntimeError("YouTube is not connected — run: "
                           "python -m app.bot.cli connect youtube")

    fresh_enough = (
        record.get("access_token")
        and int(record.get("expires_at") or 0) > int(time.time()) + 60
    )
    if fresh_enough and not force:
        return str(record["access_token"])

    client_id, client_secret = client_credentials()
    body = _post_token(
        {
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": record["refresh_token"],
            "grant_type": "refresh_token",
        }
    )
    tokens.put(
        PLATFORM,
        access_token=body.get("access_token", ""),
        expires_at=int(time.time()) + int(body.get("expires_in", 3600)),
    )
    return str(body.get("access_token", ""))


def ready() -> tuple[bool, str]:
    ok, reason = tokens.configured(PLATFORM)
    if not ok:
        return False, reason
    client_id, client_secret = client_credentials()
    if not client_id or not client_secret:
        return False, "missing direct.youtube_client_id / youtube_client_secret"
    return True, ""


def publish(video_path: str, caption, **options) -> DirectResult:
    if not os.path.isfile(video_path):
        return DirectResult(False, f"video not found: {video_path}")

    title = (getattr(caption, "title", "") or "New video")[:100]
    description = getattr(caption, "description", "") or ""
    tags = [str(tag).lstrip("#") for tag in (getattr(caption, "hashtags", None) or [])][:15]
    privacy = str(options.get("privacy_status") or "public")
    made_for_kids = bool(options.get("made_for_kids"))

    try:
        token = access_token()
    except Exception as exc:  # noqa: BLE001 - surface the reason to the user
        return DirectResult(False, str(exc))

    metadata = {
        "snippet": {
            "title": title,
            "description": description[:5000],
            "tags": tags,
            "categoryId": str(options.get("category_id") or "22"),
        },
        "status": {
            "privacyStatus": privacy,
            "selfDeclaredMadeForKids": made_for_kids,
        },
    }

    session = requests.post(
        UPLOAD_URL,
        params={"uploadType": "resumable", "part": "snippet,status"},
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=UTF-8",
            "X-Upload-Content-Type": "video/*",
            "X-Upload-Content-Length": str(os.path.getsize(video_path)),
        },
        data=json.dumps(metadata),
        timeout=TIMEOUT,
    )
    if session.status_code >= 400:
        return DirectResult(False, _explain(session))

    location = session.headers.get("Location", "")
    if not location:
        return DirectResult(False, "YouTube did not return an upload URL")

    try:
        with open(video_path, "rb") as handle:
            size = os.path.getsize(video_path)
            upload = requests.put(
                location,
                data=handle,
                headers={
                    "Content-Type": "video/*",
                    "Content-Length": str(size),
                },
                timeout=1800,
            )
    except OSError as exc:
        return DirectResult(False, f"cannot read the video file: {exc}")

    if upload.status_code >= 400:
        return DirectResult(False, _explain(upload))

    body = upload.json() if upload.content else {}
    video_id = str((body.get("id") if isinstance(body, dict) else "") or "")
    if not video_id:
        return DirectResult(False, "YouTube accepted the upload but returned no video id")

    logger.info(f"youtube upload ok: {video_id}")
    return DirectResult(True, f"uploaded {video_id}", url=f"https://youtu.be/{video_id}")


def _explain(response) -> str:
    """Turn Google's error envelope into something a person can act on."""
    try:
        body = response.json()
    except ValueError:
        return f"HTTP {response.status_code}: {response.text[:300]}"

    error = body.get("error") if isinstance(body, dict) else None
    if isinstance(error, dict):
        message = str(error.get("message") or "")
        reasons = [
            str(item.get("reason") or "")
            for item in (error.get("errors") or [])
            if isinstance(item, dict)
        ]
        if any("quota" in reason for reason in reasons):
            return (
                "the daily YouTube upload quota is used up (6 uploads/day on the "
                "free API) — the rest will go tomorrow"
            )
        if "invalid_grant" in message or any("auth" in reason for reason in reasons):
            return (
                "YouTube refused the login — run: "
                "python -m app.bot.cli connect youtube"
            )
        return message or f"HTTP {response.status_code}"
    return f"HTTP {response.status_code}: {response.text[:300]}"


class YouTubeTarget:
    platform = Platform.youtube

    def ready(self) -> tuple[bool, str]:
        return ready()

    def publish(self, video_path: str, caption) -> DirectResult:
        return publish(video_path, caption)
