"""按平台生成发布文案。

复用 ``app/services/llm.py`` 已有的 ``generate_social_metadata``（它已经为四条
渠道定义了标题长度、正文长度和话题标签数量的差异），并提供纯本地的启发式降级，
保证没有配置任何模型密钥时机器人依然能给出可用文案。
"""

from __future__ import annotations

import re

from loguru import logger

from app.bot.models import Caption, Platform

# 与 app/services/llm.py 的 SOCIAL_PLATFORMS 保持一致。这里保留一份副本，
# 只是为了在无法导入该模块（例如未安装 openai 依赖）时仍能做截断。
_LIMITS: dict[Platform, tuple[int, int, int]] = {
    Platform.tiktok: (100, 2200, 5),
    Platform.instagram: (125, 2200, 8),
    Platform.facebook: (125, 2200, 5),
    Platform.youtube: (100, 5000, 3),
}

_STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "of", "to", "in", "on", "for", "with",
    "how", "why", "what", "when", "where", "who", "is", "are", "was", "were",
    "be", "been", "being", "your", "you", "my", "me", "we", "our", "they",
    "this", "that", "these", "those", "it", "its", "from", "by", "at", "as",
    "about", "into", "keep", "keeps", "kept", "get", "gets", "got", "make",
    "makes", "made", "do", "does", "did", "doing", "have", "has", "had",
    "will", "would", "can", "could", "should", "just", "more", "most", "very",
    "than", "then", "also", "some", "any", "all", "not", "no", "yes", "here",
    "there", "now", "today", "still", "even", "only", "much", "many", "one",
    "before", "after", "during", "while", "thing", "things", "check", "checks",
    "second", "seconds", "minute", "minutes", "hour", "hours",
}

# 每个平台最有效的发现型标签用不同写法，避免四条渠道文案完全雷同。
_PRIMARY_TAG = {
    Platform.tiktok: "#FYP",
    Platform.instagram: "#Reels",
    Platform.facebook: "#Reels",
    Platform.youtube: "#Shorts",
}

# 以 -ing / -ed 结尾的词在英文标题里绝大多数是动词，很难成为有意义的话题标签。
_VERB_SUFFIXES = ("ing", "ed")


def _is_tag_candidate(word: str) -> bool:
    lowered = word.lower()
    if len(lowered) < 4 or lowered in _STOPWORDS:
        return False
    return not (lowered.endswith(_VERB_SUFFIXES) and len(lowered) <= 9)


def _clean_hashtag(value: str) -> str:
    """把任意短语转成 ``#PascalCase`` 形式的话题标签。"""
    words = re.findall(r"[0-9A-Za-z\u00c0-\u024f\u4e00-\u9fff]+", str(value or ""))
    words = [word for word in words if _is_tag_candidate(word)]
    if not words:
        return ""
    joined = "".join(word[:1].upper() + word[1:] for word in words[:2])
    return f"#{joined}" if joined else ""


def _clip(text: str, limit: int) -> str:
    text = (text or "").strip()
    if limit <= 0 or len(text) <= limit:
        return text
    # 优先在词边界截断，避免把单词切成两半。
    cut = text[: limit - 1]
    space = cut.rfind(" ")
    if space > limit * 0.6:
        cut = cut[:space]
    return cut.rstrip() + "…"


def _heuristic_caption(topic: str, script: str, platform: Platform) -> Caption:
    """不依赖任何外部服务的兜底文案。"""
    title_limit, _, tag_count = _LIMITS[platform]
    subject = " ".join(str(topic or "").split()).strip() or "New short video"

    # 脚本第一句通常是最好的钩子。
    first_sentence = ""
    for chunk in re.split(r"(?<=[.!?。！？])\s+", str(script or "")):
        chunk = chunk.strip()
        if len(chunk) >= 12:
            first_sentence = chunk
            break

    description = first_sentence or subject
    tags: list[str] = []
    for candidate in [subject, first_sentence]:
        for word in re.findall(r"[A-Za-z0-9\u00c0-\u024f\u4e00-\u9fff]{4,}", candidate):
            tag = _clean_hashtag(word)
            if tag and tag not in tags:
                tags.append(tag)
            if len(tags) >= max(1, tag_count - 1):
                break
        if len(tags) >= max(1, tag_count - 1):
            break
    # 始终给平台留一个通用的发现型标签，而不是把坑位浪费在第三个弱主题词上。
    primaries = [_PRIMARY_TAG[platform], "#Shorts", "#Reels", "#Video"]
    for fallback in primaries:
        if len(tags) >= tag_count:
            break
        if fallback not in tags:
            tags.append(fallback)

    return Caption(
        title=_clip(subject, title_limit),
        description=_clip(description, 2200),
        hashtags=tags,
    )


def _llm_available() -> bool:
    """判断当前 LLM Provider 是否真的能用。

    没有密钥时 ``generate_social_metadata`` 不会报错，而是返回它自己的通用兜底
    文案（#shorts #viral #trending…），标签和主题无关。与其付 4 次注定失败的
    调用换回这种结果，不如直接用本模块基于主题的启发式文案。
    """
    try:
        from app.config import config
        from app.models.llm_provider import DEFAULT_LLM_PROVIDER_ID, get_llm_provider

        provider_id = str(
            config.app.get("llm_provider") or DEFAULT_LLM_PROVIDER_ID
        ).strip()
        spec = get_llm_provider(provider_id)
        if spec is None:
            return True  # 未知 Provider 交给真正的调用去报错
        if not spec.requires_api_key:
            return True  # ollama / claude_code 等本地或订阅式入口
        api_key = str(config.app.get(f"{provider_id}_api_key", "") or "").strip()
        return bool(api_key)
    except Exception:  # noqa: BLE001 - 探测失败时保持乐观，让正常调用路径决定
        return True


def _from_llm(topic: str, script: str, platform: Platform, language: str) -> Caption:
    """调用现有的社交文案生成器；任何异常都让调用方降级。"""
    from app.services import llm  # 延迟导入：未安装 openai 时也能用启发式文案

    metadata = llm.generate_social_metadata(
        video_subject=topic,
        video_script=script,
        language=language or "auto",
        platform=platform.social_key,
    )
    if not isinstance(metadata, dict):
        raise TypeError("social metadata is not a mapping")

    title = str(metadata.get("title") or "").strip()
    description = str(metadata.get("caption") or metadata.get("description") or "").strip()
    raw_tags = metadata.get("hashtags") or []
    if isinstance(raw_tags, str):
        raw_tags = raw_tags.split()
    hashtags: list[str] = []
    for item in raw_tags:
        tag = str(item).strip()
        if not tag:
            continue
        if not tag.startswith("#"):
            tag = f"#{tag.lstrip('#')}"
        if tag not in hashtags:
            hashtags.append(tag)

    if not title and not description:
        raise ValueError("social metadata is empty")

    title_limit, _, tag_count = _LIMITS[platform]
    return Caption(
        title=_clip(title or description, title_limit),
        description=_clip(description or title, 2200),
        hashtags=hashtags[:tag_count],
    )


def adapt(caption: Caption, platform: Platform) -> Caption:
    """把一份文案裁剪到指定平台的长度与标签数量上限。"""
    title_limit, body_limit, tag_count = _LIMITS[platform]
    return Caption(
        title=_clip(caption.title, title_limit),
        description=_clip(caption.description, body_limit),
        hashtags=list(caption.hashtags[:tag_count]),
    )


def generate_captions(
    topic: str,
    script: str,
    platforms: list[Platform],
    language: str = "auto",
) -> dict[str, Caption]:
    """为每个平台生成文案。

    第一次调用失败后立刻停止尝试模型，剩下的平台使用启发式结果，避免在
    没有配置密钥的机器上因为重试而让机器人卡住。
    """
    if not platforms:
        return {}

    captions: dict[str, Caption] = {}
    llm_broken = not _llm_available()
    if llm_broken:
        logger.info("no LLM key configured; using topic-based caption heuristics")
    for platform in platforms:
        if not llm_broken:
            try:
                captions[platform.value] = _from_llm(topic, script, platform, language)
                continue
            except Exception as exc:  # noqa: BLE001 - 任何失败都应降级而非中断
                logger.warning(f"bot caption generation failed, using fallback: {exc}")
                llm_broken = True
        captions[platform.value] = _heuristic_caption(topic, script, platform)
    return captions


def regenerate(caption: Caption, topic: str, platform: Platform) -> Caption:
    """用户点“换一个文案”时使用的轻量变体：在原文案前换一个开场钩子。"""
    hooks = ("Worth 60 seconds of your time:", "Here is what nobody tells you:", "Quick one:")
    existing = caption.description.strip()
    hook = next((item for item in hooks if not existing.startswith(item)), hooks[0])
    return adapt(
        Caption(
            title=caption.title,
            description=f"{hook} {existing}".strip(),
            hashtags=list(caption.hashtags),
        ),
        platform,
    )
