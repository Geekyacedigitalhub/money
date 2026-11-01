"""四条渠道的发布规则速查。

这些限制直接决定“做多长、怎么发、能不能赚到钱”，所以在机器人里单独成模块，
既用于 /how 命令，也用于参数校验提示（例如 TikTok 想要广告分成必须 ≥1 分钟）。
"""

from __future__ import annotations

from dataclasses import dataclass

from app.bot.models import Platform


@dataclass(frozen=True)
class PlatformGuide:
    platform: Platform
    max_seconds: int
    aspect: str
    account_requirement: str
    monetization: str
    gotcha: str
    # 想要拿到该平台广告分成时的最短时长要求（秒），0 表示没有该门槛。
    reward_min_seconds: int = 0


GUIDES: dict[Platform, PlatformGuide] = {
    Platform.tiktok: PlatformGuide(
        platform=Platform.tiktok,
        max_seconds=600,
        aspect="9:16",
        account_requirement="Personal account (business accounts are excluded from rewards).",
        monetization="Creator Rewards is limited to US, UK, DE, FR, JP, KR, MX, BR.",
        gotcha=(
            "Only videos OVER 1 minute qualify for Creator Rewards, and only views "
            "from those 8 countries pay. From Nigeria: use TikTok for reach, then "
            "convert with affiliate links or brand deals — not ad revenue."
        ),
        reward_min_seconds=61,
    ),
    Platform.youtube: PlatformGuide(
        platform=Platform.youtube,
        max_seconds=180,
        aspect="9:16 (Shorts) or 16:9 (long-form)",
        account_requirement="Channel verified with a phone number; 2-step verification on.",
        monetization="YouTube Partner Program — Nigeria IS eligible (paid via AdSense).",
        gotcha=(
            "Shorts must be ≤3 minutes. Shorts ad revenue needs ~10M views/90 days, so "
            "use Shorts for discovery and put long-form on the same channel for the "
            "actual ad money."
        ),
    ),
    Platform.instagram: PlatformGuide(
        platform=Platform.instagram,
        max_seconds=90,
        aspect="9:16",
        account_requirement="Professional (Creator/Business) account for API publishing.",
        monetization="No per-view payout. Earn via brand deals, affiliate, own products.",
        gotcha=(
            "Reels over 90 seconds get down-ranked. Put the hook in the first 1.5 "
            "seconds — that is where the scroll decision happens."
        ),
    ),
    Platform.facebook: PlatformGuide(
        platform=Platform.facebook,
        max_seconds=90,
        aspect="9:16",
        account_requirement="A Facebook Page (not a personal profile).",
        monetization=(
            "Facebook Content Monetization — one of the few short-form programmes "
            "that pays Nigerian creators."
        ),
        gotcha=(
            "Needs roughly 10k Page followers plus ~600k minutes viewed in 60 days. "
            "Re-uploads with a TikTok watermark get flagged as unoriginal."
        ),
    ),
}


def format_guides() -> str:
    lines = ["📏 **What each platform actually accepts**", ""]
    for platform, guide in GUIDES.items():
        lines.append(f"{platform.emoji} **{platform.label}** — max {guide.max_seconds}s, {guide.aspect}")
        lines.append(f"  • Account: {guide.account_requirement}")
        lines.append(f"  • Money: {guide.monetization}")
        lines.append(f"  • Watch out: {guide.gotcha}")
        lines.append("")
    lines.append(
        "_Set the target length with `[bot] video_aspect` and by writing longer "
        "scripts (paragraph_number). TikTok needs 60s+ for rewards._"
    )
    return "\n".join(lines)


def suggest_duration_warning(platforms: list[Platform], seconds: float) -> str:
    """在发布前提示“这条视频在某个平台上拿不到分成”。"""
    if seconds <= 0:
        return ""
    for platform in platforms:
        guide = GUIDES.get(platform)
        if guide and guide.reward_min_seconds and seconds < guide.reward_min_seconds:
            return (
                f"{platform.label}: this clip is under {guide.reward_min_seconds}s, "
                "so it cannot earn Creator Rewards. Fine if you only want reach."
            )
    return ""
