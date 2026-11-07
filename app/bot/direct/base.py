"""Shared shapes for the free, direct publishing path.

The paid route (Upload-Post) posts to all four platforms through one API. This
package talks to each platform's own API instead: no subscription, but each
platform brings its own auth model, so each gets its own small client.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.bot.models import Platform


@dataclass
class DirectResult:
    ok: bool
    detail: str = ""
    url: str = ""


class DirectTarget(Protocol):
    platform: Platform

    def ready(self) -> tuple[bool, str]: ...

    def publish(self, video_path: str, caption) -> DirectResult: ...


def _caption_text(caption) -> str:
    """One blob of text for APIs that take a single description field."""
    if caption is None:
        return ""
    parts = [str(caption.title or "").strip(), str(caption.description or "").strip()]
    tags = " ".join(str(tag) for tag in (caption.hashtags or []))
    if tags:
        parts.append(tags)
    return "\n\n".join(part for part in parts if part).strip()
