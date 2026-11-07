"""Direct publishing: each platform's own API, no paid middleman.

Pick it with ``[bot] delivery = "direct"``. Platforms without a connected token
are reported as skipped, never silently dropped — a video that did not go out is
the one thing this bot must never pretend about.
"""

from __future__ import annotations

from loguru import logger

from app.bot.direct import facebook, youtube
from app.bot.direct.base import DirectResult
from app.bot.models import Platform
from app.bot.publisher import PlatformResult

# Instagram and TikTok need an extra gate (a public video URL / an audited app),
# so they are registered as they land rather than pretending they work now.
TARGETS = {
    Platform.youtube: youtube.YouTubeTarget,
    Platform.facebook: facebook.FacebookTarget,
}

SUPPORTED = tuple(TARGETS)


def target_for(platform: Platform):
    factory = TARGETS.get(platform)
    return factory() if factory else None


def status_lines() -> list[tuple[Platform, bool, str]]:
    rows: list[tuple[Platform, bool, str]] = []
    for platform in Platform:
        target = target_for(platform)
        if target is None:
            rows.append((platform, False, "not available on the free direct path"))
            continue
        ok, reason = target.ready()
        rows.append((platform, ok, reason))
    return rows


def ready_count() -> int:
    return sum(1 for _platform, ok, _reason in status_lines() if ok)


class DirectPublisher:
    """Publisher protocol implementation backed by the per-platform clients."""

    name = "direct"

    @property
    def live(self) -> bool:
        return ready_count() > 0

    def publish(self, video_path, captions, platforms, settings) -> list[PlatformResult]:
        results: list[PlatformResult] = []

        for platform in platforms:
            target = target_for(platform)
            if target is None:
                results.append(
                    PlatformResult(
                        platform,
                        False,
                        "the free direct path does not cover this platform yet — "
                        "connect Upload-Post for it, or use /week and post it yourself",
                    )
                )
                continue

            ok, reason = target.ready()
            if not ok:
                results.append(PlatformResult(platform, False, reason))
                continue

            caption = captions.get(platform.value) or captions.get(platform.social_key)
            try:
                if platform is Platform.youtube:
                    result = youtube.publish(
                        video_path,
                        caption,
                        privacy_status=settings.youtube_privacy_status,
                        made_for_kids=settings.youtube_made_for_kids,
                    )
                else:
                    result = target.publish(video_path, caption)
            except Exception as exc:  # noqa: BLE001 - one platform must not stop the rest
                logger.exception(f"direct publish to {platform.value} crashed")
                results.append(
                    PlatformResult(platform, False, f"{type(exc).__name__}: {exc}")
                )
                continue

            results.append(
                PlatformResult(platform, result.ok, result.detail, url=result.url)
            )

        return results


def missing_setup() -> list[str]:
    """Human-readable list of what stops the direct path from posting."""
    notes: list[str] = []
    for platform, ok, reason in status_lines():
        if ok:
            continue
        if platform in TARGETS:
            notes.append(f"{platform.value}: {reason}")
    return notes


__all__ = [
    "SUPPORTED",
    "TARGETS",
    "DirectPublisher",
    "DirectResult",
    "missing_setup",
    "ready_count",
    "status_lines",
    "target_for",
]
