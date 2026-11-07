"""发布层：把成片送到 Facebook / YouTube / TikTok / Instagram。

默认使用仓库已有的 Upload-Post 集成（一个 Key 覆盖四个平台）。当它没有配置
时，发布器自动进入“模拟发布”，把完整的发布载荷写成 JSON 收据，让用户在没有
任何第三方账号的情况下也能验证机器人流程是否正确。

模拟发布从不上传任何内容，也不会让用户以为自己已经发出去了 —— 每一条回执都
明确标注 SIMULATED。
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Protocol

from loguru import logger

from app.bot.config import BotSettings, publish_account_ready
from app.bot.models import ALL_PLATFORMS, Caption, Platform


@dataclass
class PlatformResult:
    platform: Platform
    success: bool
    detail: str = ""
    simulated: bool = False
    # 平台返回的帖子链接（如果有）。用户要的就是这个 —— 点开就能看到成片。
    url: str = ""


def extract_url(payload: object, depth: int = 0) -> str:
    """从各家平台结构不一的返回里捞出第一个可点击的帖子链接。"""
    if depth > 4:
        return ""
    if isinstance(payload, dict):
        for key in ("url", "post_url", "permalink", "share_url", "video_url"):
            value = payload.get(key)
            if isinstance(value, str) and value.startswith("http"):
                return value
        for value in payload.values():
            found = extract_url(value, depth + 1)
            if found:
                return found
    elif isinstance(payload, list):
        for value in payload:
            found = extract_url(value, depth + 1)
            if found:
                return found
    elif isinstance(payload, str) and payload.startswith("http"):
        return payload
    return ""


class Publisher(Protocol):
    name: str

    @property
    def live(self) -> bool: ...

    def publish(
        self,
        video_path: str,
        captions: dict[str, Caption],
        platforms: list[Platform],
        settings: BotSettings,
    ) -> list[PlatformResult]: ...


class UploadPostPublisher:
    """通过 Upload-Post 发布到四个平台。"""

    name = "upload-post"

    @property
    def live(self) -> bool:
        ready, _ = publish_account_ready()
        return ready

    def publish(
        self,
        video_path: str,
        captions: dict[str, Caption],
        platforms: list[Platform],
        settings: BotSettings,
    ) -> list[PlatformResult]:
        from app.services.upload_post import upload_post_service

        results: list[PlatformResult] = []
        for platform in platforms:
            caption = captions.get(platform.value) or captions.get(platform.social_key)
            title = (caption.title if caption else "") or "New video"
            description = caption.description if caption else ""

            youtube_extra = None
            if platform is Platform.youtube:
                youtube_extra = {
                    "youtube_title": title[:100],
                    "youtube_description": description,
                    "tags": [tag.lstrip("#") for tag in (caption.hashtags if caption else [])],
                    "privacyStatus": settings.youtube_privacy_status,
                    "selfDeclaredMadeForKids": bool(settings.youtube_made_for_kids),
                }

            try:
                # 每个平台单独提交：四个渠道的文案长度和话题标签数量不同，
                # 合并成一次提交就只能共用一份文案。
                result = upload_post_service.upload_video(
                    video_path=video_path,
                    title=title,
                    platforms=[platform.upload_post_id],
                    privacy_level="PUBLIC_TO_EVERYONE",
                    youtube_extra=youtube_extra,
                )
            except Exception as exc:  # noqa: BLE001 - 单个平台失败不应中断其它平台
                logger.exception(f"publish to {platform.value} crashed: {exc}")
                results.append(
                    PlatformResult(platform, False, f"{type(exc).__name__}: {exc}")
                )
                continue

            success = bool(result.get("success"))
            detail = ""
            if not success:
                detail = str(
                    result.get("error")
                    or result.get("message")
                    or "the platform rejected the upload"
                )
            results.append(
                PlatformResult(
                    platform, success, detail, url=extract_url(result) if success else ""
                )
            )
        return results


class SimulatedPublisher:
    """把发布载荷写到磁盘，绝不联网。"""

    name = "simulated"

    @property
    def live(self) -> bool:
        return False

    def __init__(self, receipt_dir: str | None = None) -> None:
        self.receipt_dir = receipt_dir

    def publish(
        self,
        video_path: str,
        captions: dict[str, Caption],
        platforms: list[Platform],
        settings: BotSettings,
    ) -> list[PlatformResult]:
        if self.receipt_dir:
            os.makedirs(self.receipt_dir, exist_ok=True)
            receipt = {
                "simulated": True,
                "created_at": time.time(),
                "video": os.path.basename(video_path),
                "video_bytes": os.path.getsize(video_path) if os.path.isfile(video_path) else 0,
                "payload": [
                    {
                        "platform": platform.value,
                        "upload_post_platform": platform.upload_post_id,
                        "title": (captions.get(platform.value).title if captions.get(platform.value) else ""),
                        "description": (
                            captions.get(platform.value).description
                            if captions.get(platform.value)
                            else ""
                        ),
                        "hashtags": (
                            list(captions.get(platform.value).hashtags)
                            if captions.get(platform.value)
                            else []
                        ),
                    }
                    for platform in platforms
                ],
            }
            path = os.path.join(
                self.receipt_dir, f"receipt-{int(time.time() * 1000)}.json"
            )
            try:
                with open(path, "w", encoding="utf-8") as handle:
                    json.dump(receipt, handle, ensure_ascii=False, indent=2)
            except OSError as exc:
                logger.warning(f"failed to write simulated publish receipt: {exc}")

        return [
            PlatformResult(
                platform,
                True,
                "SIMULATED — nothing was uploaded. Configure Upload-Post to publish for real.",
                simulated=True,
            )
            for platform in platforms
        ]


def build_publisher(settings: BotSettings, receipt_dir: str | None = None) -> Publisher:
    """有配置就用真实发布器，否则用模拟发布器。"""
    if settings.delivery == "direct":
        # 免费直连：各平台自己的 API，没有订阅费。导入放在这里，
        # app.bot.direct 反过来要用本模块的 PlatformResult。
        from app.bot.direct import DirectPublisher

        direct = DirectPublisher()
        if direct.live:
            return direct
        logger.info("no platform is connected for direct publishing; simulating")
        return SimulatedPublisher(receipt_dir)

    publisher = UploadPostPublisher()
    if publisher.live:
        return publisher
    logger.info("Upload-Post is not configured; bot will use simulated publishing")
    return SimulatedPublisher(receipt_dir)


def readiness_report(settings: BotSettings) -> list[tuple[Platform, str]]:
    """给 ``/platforms`` 命令用的逐平台状态说明。"""
    ready, reason = publish_account_ready()
    lines: list[tuple[Platform, str]] = []
    for platform in ALL_PLATFORMS:
        if ready:
            lines.append((platform, "live — will post for real"))
        else:
            lines.append((platform, f"simulated ({reason})"))
    return lines
