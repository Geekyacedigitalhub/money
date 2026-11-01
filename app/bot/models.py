"""机器人的领域模型。

这里的对象刻意保持朴素（dataclass / Enum），不依赖 FastAPI 或 Telegram，
让同一个内核既能跑在网页聊天里，也能跑在 Telegram 上，并且可以在测试中
直接用假对象替换。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any
from uuid import uuid4


class Platform(str, Enum):
    """支持的四条发布渠道。

    取值同时充当机器人的内部平台 ID。``social_key`` 对应
    ``app/services/llm.py`` 的文案规格，``upload_post_id`` 对应
    Upload-Post 的平台 ID，两者历史上使用了不同拼写，集中在这里映射，
    避免各处重复硬编码。
    """

    tiktok = "tiktok"
    instagram = "instagram"
    facebook = "facebook"
    youtube = "youtube"

    @property
    def social_key(self) -> str:
        return _SOCIAL_KEYS[self]

    @property
    def upload_post_id(self) -> str:
        return _UPLOAD_POST_IDS[self]

    @property
    def label(self) -> str:
        return _LABELS[self]

    @property
    def emoji(self) -> str:
        return _EMOJI[self]

    def button_label(self, selected: bool) -> str:
        return f"{'✅' if selected else '⬜'} {self.emoji} {self.label}"


_SOCIAL_KEYS = {
    Platform.tiktok: "tiktok",
    Platform.instagram: "instagram_reels",
    Platform.facebook: "facebook_reels",
    Platform.youtube: "youtube_shorts",
}

_UPLOAD_POST_IDS = {
    Platform.tiktok: "tiktok",
    Platform.instagram: "instagram",
    Platform.facebook: "facebook",
    Platform.youtube: "youtube",
}

_LABELS = {
    Platform.tiktok: "TikTok",
    Platform.instagram: "Instagram",
    Platform.facebook: "Facebook",
    Platform.youtube: "YouTube",
}

_EMOJI = {
    Platform.tiktok: "🎵",
    Platform.instagram: "📸",
    Platform.facebook: "👥",
    Platform.youtube: "▶️",
}

ALL_PLATFORMS = tuple(Platform)

# 自动运营（autopilot）用这个身份提交任务：它需要通过 allow-list 检查，
# 但不应该和任何真实用户混在一起。
SYSTEM_USER = "__autopilot__"


def parse_platform(value: Any) -> Platform | None:
    """把用户输入或配置值转成 ``Platform``，无法识别时返回 ``None``。"""
    if isinstance(value, Platform):
        return value
    text = str(value or "").strip().lower()
    if not text:
        return None
    aliases = {
        "tiktok": Platform.tiktok,
        "tik_tok": Platform.tiktok,
        "ig": Platform.instagram,
        "insta": Platform.instagram,
        "instagram": Platform.instagram,
        "instagram_reels": Platform.instagram,
        "reels": Platform.instagram,
        "fb": Platform.facebook,
        "facebook": Platform.facebook,
        "facebook_reels": Platform.facebook,
        "yt": Platform.youtube,
        "youtube": Platform.youtube,
        "youtube_shorts": Platform.youtube,
        "shorts": Platform.youtube,
    }
    return aliases.get(text)


# --------------------------------------------------------------------------
# 对话消息
# --------------------------------------------------------------------------


@dataclass
class Button:
    """一个可点击的按钮，所有渠道共用的最小表达。"""

    label: str
    data: str


@dataclass
class ChatMessage:
    """任何渠道进来的统一消息。"""

    chat_id: str
    text: str = ""
    user_id: str = ""
    display_name: str = ""
    # 用户直接发来的视频文件（本地路径）；有值时跳过生成直接进入发布流程。
    attachment: str | None = None
    # 按钮回调载荷。有值时 ``text`` 一般为空。
    callback: str | None = None


@dataclass
class Reply:
    """内核要发回去的东西。适配器负责把它渲染成具体的平台 API 调用。"""

    text: str = ""
    # 本地视频文件路径。存在时适配器应优先发送视频而不是纯文本。
    video: str | None = None
    # 每一行是一排按钮。
    buttons: list[list[Button]] = field(default_factory=list)
    # 可选封面，便于网页在视频加载前先显示。
    poster: str | None = None
    # 仅用于网页端：把这段回复关联到某个任务，方便前端轮询进度。
    job_id: str | None = None


# --------------------------------------------------------------------------
# 生成 / 发布任务
# --------------------------------------------------------------------------


class JobState(str, Enum):
    queued = "queued"
    generating = "generating"
    ready = "ready"
    publishing = "publishing"
    published = "published"
    failed = "failed"


@dataclass
class Caption:
    """某条渠道最终使用的文案。"""

    title: str = ""
    description: str = ""
    hashtags: list[str] = field(default_factory=list)

    def full_text(self, *, include_title: bool = False) -> str:
        body = self.description.strip()
        if include_title and self.title.strip() and self.title.strip() not in body:
            body = f"{self.title.strip()}\n\n{body}" if body else self.title.strip()
        tags = " ".join(tag for tag in self.hashtags if tag)
        if tags:
            body = f"{body}\n\n{tags}" if body else tags
        return body.strip()


@dataclass
class Job:
    """一次“生成 + 可选发布”的完整记录。"""

    id: str = field(default_factory=lambda: uuid4().hex)
    chat_id: str = ""
    topic: str = ""
    script: str = ""
    state: JobState = JobState.queued
    progress: int = 0
    message: str = ""
    video: str | None = None
    poster: str | None = None
    error: str = ""
    # 平台 -> 文案。生成完成时按当前选中的渠道填充。
    captions: dict[str, Caption] = field(default_factory=dict)
    # 平台 -> 发布结果说明，发布后填充。
    results: dict[str, str] = field(default_factory=dict)
    platforms: list[str] = field(default_factory=list)
    simulated: bool = False
    task_id: str = ""
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def touch(self, **changes: Any) -> Job:
        for key, value in changes.items():
            setattr(self, key, value)
        self.updated_at = time.time()
        return self

    @property
    def is_terminal(self) -> bool:
        return self.state in {JobState.ready, JobState.published, JobState.failed}

    def platform_objects(self) -> list[Platform]:
        found: list[Platform] = []
        for value in self.platforms:
            platform = parse_platform(value)
            if platform is not None and platform not in found:
                found.append(platform)
        return found

    def to_dict(self) -> dict[str, Any]:
        """给前端 / 测试使用的安全序列化（不含本地绝对路径以外的机密）。"""
        return {
            "id": self.id,
            "chat_id": self.chat_id,
            "topic": self.topic,
            "state": self.state.value,
            "progress": self.progress,
            "message": self.message,
            "error": self.error,
            "has_video": bool(self.video),
            "captions": {
                key: {
                    "title": cap.title,
                    "description": cap.description,
                    "hashtags": list(cap.hashtags),
                }
                for key, cap in self.captions.items()
            },
            "results": dict(self.results),
            "platforms": list(self.platforms),
            "simulated": self.simulated,
            "created_at": self.created_at,
        }


@dataclass
class Session:
    """每个聊天窗口的偏好，跨消息保持。"""

    chat_id: str
    platforms: list[str] = field(default_factory=lambda: [p.value for p in ALL_PLATFORMS])
    last_job_id: str = ""
    auto_publish: bool = False

    def selected(self) -> list[Platform]:
        found: list[Platform] = []
        for value in self.platforms:
            platform = parse_platform(value)
            if platform is not None and platform not in found:
                found.append(platform)
        return found

    def toggle(self, platform: Platform) -> bool:
        """切换一个渠道，返回切换后是否处于选中状态。"""
        current = self.selected()
        if platform in current:
            current = [item for item in current if item is not platform]
            state = False
        else:
            current.append(platform)
            state = True
        # 保持固定顺序，避免按钮顺序随点击历史漂移。
        order = {item: index for index, item in enumerate(ALL_PLATFORMS)}
        self.platforms = [item.value for item in sorted(current, key=order.get)]
        return state
