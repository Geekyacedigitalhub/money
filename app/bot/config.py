"""机器人配置读取。

所有设置都可以写在 ``config.toml`` 的 ``[bot]`` 段里，也可以用环境变量覆盖，
方便部署到容器或云主机时不把密钥写进文件。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from typing import Any

from app.bot.models import ALL_PLATFORMS, Platform, parse_platform
from app.config import config


def _as_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on", "y"}:
        return True
    if text in {"0", "false", "no", "off", "n", ""}:
        return False
    return default


def _as_int(value: Any, default: int) -> int:
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return default
    return parsed


def _as_float(value: Any, default: float) -> float:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return default


def _as_platform_list(value: Any, default: list[str]) -> list[str]:
    """把配置里的渠道写法统一成稳定的 ``Platform`` 取值列表。"""
    if isinstance(value, str):
        candidates: list[Any] = [part for part in value.replace(",", " ").split()]
    elif isinstance(value, (list, tuple, set)):
        candidates = list(value)
    else:
        candidates = []

    found: list[Platform] = []
    for item in candidates:
        platform = parse_platform(item)
        if platform is not None and platform not in found:
            found.append(platform)
    if not found:
        return list(default)
    order = {item: index for index, item in enumerate(ALL_PLATFORMS)}
    return [item.value for item in sorted(found, key=order.get)]


def _as_times(value: Any, default: tuple[str, ...]) -> tuple[str, ...]:
    """把 ``"08:00, 19:30"`` 或列表统一成 ``("08:00", "19:30")``。"""
    if isinstance(value, str):
        items = [part.strip() for part in value.replace(";", ",").split(",")]
    elif isinstance(value, (list, tuple, set)):
        items = [str(part).strip() for part in value]
    else:
        items = []
    valid = [item for item in items if ":" in item and item]
    return tuple(valid) or default


@dataclass(frozen=True)
class BotSettings:
    """机器人运行参数（不可变，便于安全地跨线程传递）。"""

    # 让机器人变成"你的"：聊天页标题与欢迎语都用这两个字段。
    owner_name: str = ""
    bot_name: str = ""
    enabled: bool = True
    # 演示模式：不调用 LLM / 素材 / TTS，直接用 ffmpeg 渲染一段占位视频，
    # 让整条对话与发布流程在没有密钥的机器上也能跑通。
    demo_mode: bool = False
    # 允许使用机器人的用户 ID（Telegram）或浏览器会话 ID（网页）。留空表示
    # 允许任何人 —— 只建议在本机或内网这样使用。
    allowed_users: tuple[str, ...] = ()
    default_platforms: list[str] = field(
        default_factory=lambda: [p.value for p in ALL_PLATFORMS]
    )
    # 生成完成后是否自动发布，跳过“预览再确认”。默认关闭：一条错误的成片
    # 发出去是不可撤回的。
    auto_publish: bool = False
    # 只有这些渠道全部配置好才会自动发布，避免半途失败。
    video_aspect: str = "9:16"
    video_source: str = "pexels"
    voice_name: str = "en-US-AriaNeural-Female"
    video_language: str = ""
    subtitle_enabled: bool = True
    paragraph_number: int = 1
    max_concurrent_jobs: int = 1
    # 生成超时；超时后任务标记失败，避免机器人一直显示“生成中”。
    job_timeout_seconds: int = 1800
    # 文案语言，auto 表示跟随脚本。
    caption_language: str = "auto"
    youtube_privacy_status: str = "public"
    youtube_made_for_kids: bool = False
    # Telegram
    telegram_token: str = ""
    # 网页端允许的最大上传体积（MB），用于直接转发别人做好的视频。
    max_upload_mb: int = 200
    # 发布时每个平台单独请求（各自使用自己的文案）。
    per_platform_captions: bool = True
    # --- 自动运营（autopilot）------------------------------------------
    # 常驻模式：按计划自动选题、生成、发布，不需要人一条条点。
    autopilot_enabled: bool = False
    # 0 表示按 autopilot_times 里的固定时刻跑；>0 表示每隔 N 分钟跑一次。
    autopilot_interval_minutes: int = 0
    autopilot_times: tuple[str, ...] = ("08:00", "13:00", "19:00")
    autopilot_max_per_day: int = 3
    # 两条之间的最小间隔，避免进程重启后立刻又发一条。
    autopilot_min_gap_minutes: int = 90
    # 自动选题的方向。写清楚给谁看、解决什么问题，标题才不会空泛。
    autopilot_niche: str = ""
    # 留空 = 系统本地时区。
    autopilot_timezone: str = ""
    # 生成后是否自动发布。false = 只生成，留着人工确认。
    autopilot_publish: bool = True
    # 自己的选题清单；留空 = storage/bot/autopilot/topics.txt
    autopilot_topics_file: str = ""

    @property
    def is_demo(self) -> bool:
        return self.demo_mode

    @property
    def display_name(self) -> str:
        """聊天页顶部的名字：优先用配置，其次用 owner，最后用默认值。"""
        if self.bot_name.strip():
            return self.bot_name.strip()
        if self.owner_name.strip():
            return f"{self.owner_name.strip()}'s Post Bot"
        return "Post Bot"

    @property
    def greeting_name(self) -> str:
        return self.owner_name.strip()

    def allows(self, user_id: str) -> bool:
        if not self.allowed_users:
            return True
        return str(user_id).strip() in self.allowed_users

    def with_overrides(self, **changes: Any) -> BotSettings:
        return replace(self, **changes)

    @property
    def platforms(self) -> list[Platform]:
        found = [parse_platform(value) for value in self.default_platforms]
        return [item for item in found if item is not None]


def _raw() -> dict[str, Any]:
    section = getattr(config, "bot", None)
    return dict(section) if isinstance(section, dict) else {}


def load_settings() -> BotSettings:
    """读取 ``[bot]`` 配置段并叠加环境变量覆盖。"""
    raw = _raw()
    defaults = BotSettings()

    allowed = raw.get("allowed_users", [])
    if isinstance(allowed, str):
        allowed = [part.strip() for part in allowed.replace(",", " ").split()]
    elif isinstance(allowed, (list, tuple, set)):
        allowed = [str(item).strip() for item in allowed]
    else:
        allowed = []
    allowed = [item for item in allowed if item]
    if not allowed:
        env_allowed = os.getenv("MPT_BOT_ALLOWED_USERS", "").strip()
        if env_allowed:
            allowed = [
                part.strip()
                for part in env_allowed.replace(",", " ").split()
                if part.strip()
            ]

    settings = BotSettings(
        owner_name=str(raw.get("owner_name") or "").strip(),
        bot_name=str(raw.get("bot_name") or "").strip(),
        enabled=_as_bool(raw.get("enabled"), defaults.enabled),
        demo_mode=_as_bool(raw.get("demo_mode"), defaults.demo_mode),
        allowed_users=tuple(allowed),
        default_platforms=_as_platform_list(
            raw.get("default_platforms"), defaults.default_platforms
        ),
        auto_publish=_as_bool(raw.get("auto_publish"), defaults.auto_publish),
        video_aspect=str(raw.get("video_aspect") or defaults.video_aspect),
        video_source=str(raw.get("video_source") or defaults.video_source),
        voice_name=str(raw.get("voice_name") or defaults.voice_name),
        video_language=str(raw.get("video_language") or ""),
        subtitle_enabled=_as_bool(
            raw.get("subtitle_enabled"), defaults.subtitle_enabled
        ),
        paragraph_number=max(1, _as_int(raw.get("paragraph_number"), 1)),
        max_concurrent_jobs=max(1, _as_int(raw.get("max_concurrent_jobs"), 1)),
        job_timeout_seconds=max(
            60, _as_int(raw.get("job_timeout_seconds"), defaults.job_timeout_seconds)
        ),
        caption_language=str(raw.get("caption_language") or defaults.caption_language),
        youtube_privacy_status=str(
            raw.get("youtube_privacy_status") or defaults.youtube_privacy_status
        ),
        youtube_made_for_kids=_as_bool(raw.get("youtube_made_for_kids"), False),
        telegram_token=str(
            raw.get("telegram_token") or os.getenv("MPT_BOT_TELEGRAM_TOKEN", "")
        ).strip(),
        max_upload_mb=max(1, _as_int(raw.get("max_upload_mb"), defaults.max_upload_mb)),
        per_platform_captions=_as_bool(
            raw.get("per_platform_captions"), defaults.per_platform_captions
        ),
        autopilot_enabled=_as_bool(
            raw.get("autopilot_enabled"), defaults.autopilot_enabled
        ),
        autopilot_interval_minutes=max(
            0, _as_int(raw.get("autopilot_interval_minutes"), 0)
        ),
        autopilot_times=_as_times(raw.get("autopilot_times"), defaults.autopilot_times),
        autopilot_max_per_day=max(
            1, _as_int(raw.get("autopilot_max_per_day"), defaults.autopilot_max_per_day)
        ),
        autopilot_min_gap_minutes=max(
            0,
            _as_int(
                raw.get("autopilot_min_gap_minutes"), defaults.autopilot_min_gap_minutes
            ),
        ),
        autopilot_niche=str(raw.get("autopilot_niche") or "").strip(),
        autopilot_timezone=str(raw.get("autopilot_timezone") or "").strip(),
        autopilot_publish=_as_bool(
            raw.get("autopilot_publish"), defaults.autopilot_publish
        ),
        autopilot_topics_file=str(raw.get("autopilot_topics_file") or "").strip(),
    )

    # 环境变量优先，方便容器化部署。
    if os.getenv("MPT_BOT_OWNER_NAME"):
        settings = settings.with_overrides(
            owner_name=os.getenv("MPT_BOT_OWNER_NAME", "").strip()
        )
    if os.getenv("MPT_BOT_NAME"):
        settings = settings.with_overrides(
            bot_name=os.getenv("MPT_BOT_NAME", "").strip()
        )
    if os.getenv("MPT_BOT_DEMO_MODE"):
        settings = settings.with_overrides(
            demo_mode=_as_bool(os.getenv("MPT_BOT_DEMO_MODE"), settings.demo_mode)
        )
    if os.getenv("MPT_BOT_AUTO_PUBLISH"):
        settings = settings.with_overrides(
            auto_publish=_as_bool(
                os.getenv("MPT_BOT_AUTO_PUBLISH"), settings.auto_publish
            )
        )
    if os.getenv("MPT_BOT_DEFAULT_PLATFORMS"):
        settings = settings.with_overrides(
            default_platforms=_as_platform_list(
                os.getenv("MPT_BOT_DEFAULT_PLATFORMS"), settings.default_platforms
            )
        )
    return settings


def publish_account_ready() -> tuple[bool, str]:
    """检查 Upload-Post 是否已经配置好，用于给用户明确的下一步提示。"""
    from app.config import config as runtime_config

    app_cfg = runtime_config.app
    api_key = str(app_cfg.get("upload_post_api_key", "") or "").strip()
    username = str(app_cfg.get("upload_post_username", "") or "").strip()
    enabled = _as_bool(app_cfg.get("upload_post_enabled"), False)

    missing = []
    if not enabled:
        missing.append("upload_post_enabled = true")
    if not api_key:
        missing.append("upload_post_api_key")
    if not username:
        missing.append("upload_post_username")
    if missing:
        return False, "missing " + ", ".join(missing)
    return True, ""
