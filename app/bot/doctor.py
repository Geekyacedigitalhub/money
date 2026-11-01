"""连接自检：把"我到底连上了没有"变成一条命令。

回答两个问题：

1. **账号连上了吗** —— 调用 Upload-Post 的 ``GET /api/uploadposts/users``，
   读取自己这个 profile 下的 ``social_accounts``。已连接的平台会带上句柄和
   显示名，未连接的只是一个空字符串，过期的 token 还会带 ``reauth_required``。
2. **真的能发出去吗** —— 可选地渲染一段几秒的测试视频，真实发布到一个平台，
   把平台返回的 URL 打印出来。这是唯一能 100% 证明链路通了的证据。

所有网络调用都集中在这里，方便在测试里用假响应替换。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import requests
from loguru import logger

from app.bot.config import BotSettings, publish_account_ready
from app.bot.models import ALL_PLATFORMS, Platform

API_BASE = "https://api.upload-post.com"
_TIMEOUT = 20

DASHBOARD_API_KEYS = "https://app.upload-post.com/api-keys"
DASHBOARD_MANAGE_USERS = "https://app.upload-post.com/manage-users"


@dataclass
class PlatformStatus:
    """单个平台的连接状态。"""

    platform: Platform
    connected: bool = False
    handle: str = ""
    display_name: str = ""
    needs_reauth: bool = False
    note: str = ""

    @property
    def emoji(self) -> str:
        if self.needs_reauth:
            return "⚠️"
        return "✅" if self.connected else "❌"

    def describe(self) -> str:
        if self.needs_reauth:
            return "connected but the login expired — reconnect it"
        if not self.connected:
            return self.note or "not connected"
        label = self.handle or self.display_name or "connected"
        return f"connected as {label}"


@dataclass
class ConnectionReport:
    """一次完整自检的结果。"""

    # 本地配置
    config_ready: bool = False
    config_missing: str = ""
    # Upload-Post 侧
    api_key_valid: bool | None = None
    api_error: str = ""
    profile_name: str = ""
    profile_found: bool = False
    available_profiles: list[str] = field(default_factory=list)
    plan_limit: int | None = None
    platforms: list[PlatformStatus] = field(default_factory=list)

    @property
    def connected_count(self) -> int:
        return sum(1 for item in self.platforms if item.connected and not item.needs_reauth)

    @property
    def all_connected(self) -> bool:
        return bool(self.platforms) and all(
            item.connected and not item.needs_reauth for item in self.platforms
        )

    def status_for(self, platform: Platform) -> PlatformStatus:
        for item in self.platforms:
            if item.platform is platform:
                return item
        return PlatformStatus(platform=platform)

    def to_dict(self) -> dict[str, Any]:
        return {
            "config_ready": self.config_ready,
            "config_missing": self.config_missing,
            "api_key_valid": self.api_key_valid,
            "api_error": self.api_error,
            "profile_name": self.profile_name,
            "profile_found": self.profile_found,
            "available_profiles": list(self.available_profiles),
            "plan_limit": self.plan_limit,
            "platforms": [
                {
                    "platform": item.platform.value,
                    "connected": item.connected,
                    "reauth_required": item.needs_reauth,
                    "handle": item.handle,
                    "display_name": item.display_name,
                    "note": item.note,
                }
                for item in self.platforms
            ],
        }


def _headers(api_key: str) -> dict[str, str]:
    return {"Authorization": f"Apikey {api_key}"}


def _parse_social_accounts(accounts: Any) -> list[PlatformStatus]:
    """把 ``social_accounts`` 解析成逐平台状态。

    文档里说明：值是对象表示已连接，是空字符串 / null 表示"加过这个平台但还没
    完成授权"。``reauth_required`` 为真时 token 过期，必须重新连接。
    """
    accounts = accounts if isinstance(accounts, dict) else {}
    results: list[PlatformStatus] = []
    for platform in ALL_PLATFORMS:
        raw = accounts.get(platform.upload_post_id)
        if isinstance(raw, dict):
            needs_reauth = bool(raw.get("reauth_required"))
            results.append(
                PlatformStatus(
                    platform=platform,
                    connected=True,
                    handle=str(raw.get("handle") or raw.get("username") or "").strip(),
                    display_name=str(raw.get("display_name") or "").strip(),
                    needs_reauth=needs_reauth,
                )
            )
        else:
            results.append(
                PlatformStatus(
                    platform=platform,
                    connected=False,
                    note="never connected" if raw in (None, "") else "connection incomplete",
                )
            )
    return results


def check_connection(settings: BotSettings | None = None) -> ConnectionReport:
    """执行完整自检。任何网络失败都会被转换成可读的说明，不抛异常。"""
    settings = settings or BotSettings()
    report = ConnectionReport(
        platforms=[PlatformStatus(platform=item) for item in ALL_PLATFORMS]
    )

    ready, missing = publish_account_ready()
    report.config_ready = ready
    report.config_missing = missing

    from app.config import config as runtime_config

    api_key = str(runtime_config.app.get("upload_post_api_key", "") or "").strip()
    profile = str(runtime_config.app.get("upload_post_username", "") or "").strip()
    report.profile_name = profile

    if not api_key:
        report.api_key_valid = False
        report.api_error = "no upload_post_api_key configured"
        return report
    if not profile:
        report.api_key_valid = None
        report.api_error = "no upload_post_username configured"
        return report

    # 1) 校验 Key。失败说明 Key 写错或被撤销，比"平台没连上"更容易误判。
    try:
        response = requests.get(
            f"{API_BASE}/api/uploadposts/me",
            headers=_headers(api_key),
            timeout=_TIMEOUT,
        )
        if response.status_code in (401, 403):
            report.api_key_valid = False
            report.api_error = "the API key was rejected (401/403)"
            return report
        response.raise_for_status()
        report.api_key_valid = True
    except requests.RequestException as exc:
        report.api_key_valid = None
        report.api_error = f"could not reach Upload-Post: {exc}"
        logger.warning(f"upload-post key check failed: {exc}")
        return report

    # 2) 读取所有 profile，找到配置里指定的那个。
    try:
        response = requests.get(
            f"{API_BASE}/api/uploadposts/users",
            headers=_headers(api_key),
            timeout=_TIMEOUT,
        )
        response.raise_for_status()
        body = response.json()
    except (requests.RequestException, ValueError) as exc:
        report.api_error = f"could not list profiles: {exc}"
        logger.warning(f"upload-post profile listing failed: {exc}")
        return report

    if isinstance(body, dict) and isinstance(body.get("limit"), int):
        report.plan_limit = body["limit"]

    profiles = body.get("profiles") if isinstance(body, dict) else None
    entries = [item for item in profiles or [] if isinstance(item, dict)]
    report.available_profiles = [
        str(item.get("username", "")).strip() for item in entries if item.get("username")
    ]

    # profile 名大小写在各家后台并不总是一致，先精确匹配再忽略大小写。
    match = next(
        (item for item in entries if str(item.get("username", "")).strip() == profile),
        None,
    )
    if match is None:
        match = next(
            (
                item
                for item in entries
                if str(item.get("username", "")).strip().lower() == profile.lower()
            ),
            None,
        )
    if match is None:
        report.api_error = (
            f"no profile named '{profile}' on this API key — "
            "the API key and the profile must belong to the same Upload-Post account"
        )
        return report

    report.profile_found = True
    report.profile_name = str(match.get("username", profile)).strip() or profile
    report.platforms = _parse_social_accounts(match.get("social_accounts"))
    return report


def _short_error(value: str) -> str:
    """把 requests 冗长的连接异常压成一行。"""
    text = str(value or "").strip()
    if len(text) > 160:
        text = text[:157] + "…"
    return text


def _clean_missing(value: str) -> str:
    text = str(value or "").strip()
    if text.lower().startswith("missing "):
        text = text[8:]
    return f"`{text}`"


def _plain(text: str) -> str:
    """去掉 Markdown 装饰，让终端输出也干净。"""
    return text.replace("**", "").replace("`", "")


def format_report(report: ConnectionReport, *, markdown: bool = True) -> str:
    """渲染成聊天里能直接读的文字；``markdown=False`` 用于终端。"""
    text = _format_report_markdown(report)
    return text if markdown else _plain(text)


def _format_report_markdown(report: ConnectionReport) -> str:
    lines: list[str] = []

    if not report.config_ready:
        lines.append("❌ **Not configured yet**")
        lines.append(f"Missing: {_clean_missing(report.config_missing)}")
        lines.append("")
        lines.append(f"1. Create an API key: {DASHBOARD_API_KEYS}")
        lines.append(f"2. Create a profile and connect accounts: {DASHBOARD_MANAGE_USERS}")
        lines.append("3. Paste both into `[app]` in config.toml")
        return "\n".join(lines)

    if report.api_key_valid is False:
        lines.append("❌ **API key rejected**")
        lines.append(report.api_error)
        lines.append("")
        lines.append(f"Create a new key here: {DASHBOARD_API_KEYS}")
        return "\n".join(lines)

    if report.api_key_valid is None:
        # 网络不通和"profile 不存在"是完全不同的故障，不能混为一谈：
        # 前者要查网络/代理，后者要改配置。
        lines.append("⚠️ **Could not reach Upload-Post**")
        lines.append(f"`{_short_error(report.api_error)}`")
        lines.append("")
        lines.append("Check that this machine has internet access and that")
        lines.append("`[proxy]` in config.toml is correct if you use a proxy.")
        lines.append("The key and profile are not being evaluated until this succeeds.")
        return "\n".join(lines)

    if not report.profile_found:
        lines.append("⚠️ **Profile not found**")
        lines.append(report.api_error)
        if report.available_profiles:
            lines.append("")
            lines.append(
                "Profiles on this key: "
                + ", ".join(f"`{name}`" for name in report.available_profiles[:10])
            )
            lines.append("Put the right one in `[app] upload_post_username`.")
        else:
            lines.append("")
            lines.append(f"No profiles exist yet — create one: {DASHBOARD_MANAGE_USERS}")
        return "\n".join(lines)

    lines.append(f"✅ **Connected to profile `{report.profile_name}`**")
    if report.plan_limit is not None:
        lines.append(f"Plan allows {report.plan_limit} profile(s).")
    lines.append("")

    for status in report.platforms:
        lines.append(f"{status.emoji} **{status.platform.label}** — {status.describe()}")

    lines.append("")
    if report.all_connected:
        lines.append("🎉 All four platforms are connected. You can publish for real.")
    else:
        missing = [
            item.platform.label
            for item in report.platforms
            if not item.connected or item.needs_reauth
        ]
        lines.append(f"Still to connect: **{', '.join(missing)}**")
        lines.append(f"Connect them here: {DASHBOARD_MANAGE_USERS}")
        lines.append("")
        lines.append("Accounts that cannot be connected will be *skipped* on publish,")
        lines.append("not treated as a hard failure.")

    return "\n".join(lines)


@dataclass
class TestPostResult:
    platform: Platform
    success: bool
    url: str = ""
    detail: str = ""
    simulated: bool = False

    @property
    def summary(self) -> str:
        if self.simulated:
            return (
                f"🧪 {self.platform.label}: simulated — nothing was uploaded "
                "(Upload-Post not configured)"
            )
        if self.success:
            if self.url:
                return f"✅ {self.platform.label}: posted — {self.url}"
            return f"✅ {self.platform.label}: posted (no URL returned)"
        return f"❌ {self.platform.label}: {self.detail or 'failed'}"


def send_test_post(
    settings: BotSettings,
    platform: Platform,
    seconds: int = 6,
) -> TestPostResult:
    """渲染一段几秒的测试视频并真实发布，用来证明链路是通的。

    这是唯一能区分"接口返回 200"和"内容真的出现在平台上"的手段。
    """
    import os

    from app.bot import demo
    from app.bot.models import Caption
    from app.bot.publisher import UploadPostPublisher

    ready, missing = publish_account_ready()
    if not ready:
        return TestPostResult(
            platform=platform,
            success=False,
            simulated=True,
            detail=missing,
        )

    output_dir = os.path.join(
        _storage_dir(), "diagnostics", f"test-{platform.value}"
    )
    os.makedirs(output_dir, exist_ok=True)

    video, _poster = demo.render_demo_video(
        f"Connection test — {platform.label}",
        output_dir,
        subtitle="CONNECTION TEST",
    )
    if not video:
        return TestPostResult(
            platform=platform,
            success=False,
            detail=(
                "could not render the test clip (needs Pillow + FFmpeg). "
                "Publishing may still work — check with /connect."
            ),
        )

    caption = Caption(
        title=f"Connection test ({platform.label})",
        description=(
            "Automated connection test. This post confirms the publishing "
            "pipeline works. Safe to delete."
        ),
        hashtags=["#test"],
    )

    publisher = UploadPostPublisher()
    results = publisher.publish(
        video_path=video,
        captions={platform.value: caption},
        platforms=[platform],
        settings=settings,
    )
    if not results:
        return TestPostResult(platform=platform, success=False, detail="no result returned")

    result = results[0]
    return TestPostResult(
        platform=platform,
        success=result.success,
        detail=result.detail,
        simulated=result.simulated,
        url=result.url,
    )


def _storage_dir() -> str:
    from app.utils import utils

    return utils.storage_dir(create=True)
