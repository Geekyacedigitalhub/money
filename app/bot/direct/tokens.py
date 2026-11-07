"""Token storage for direct (no-middleman) publishing.

Everything lives in ``storage/bot/tokens.json`` with 0600 permissions: a refresh
token or a Page token is a password. Nothing here ever goes into config.toml,
because config.toml gets pasted into chat screenshots.
"""

from __future__ import annotations

import json
import os
import stat
import time
from typing import Any

from loguru import logger


def _storage_dir() -> str:
    from app.utils import utils

    return utils.storage_dir(os.path.join("bot", "direct"), create=True)


def tokens_path() -> str:
    return os.path.join(_storage_dir(), "tokens.json")


def load() -> dict[str, dict[str, Any]]:
    path = tokens_path()
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning(f"cannot read {path}: {exc}")
        return {}
    return data if isinstance(data, dict) else {}


def save(data: dict[str, dict[str, Any]]) -> None:
    path = tokens_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
    # Windows ignores this; POSIX honours it. Cheap either way.
    try:
        os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass
    os.replace(tmp, path)


def get(platform: str) -> dict[str, Any]:
    return dict(load().get(platform) or {})


def put(platform: str, **fields: Any) -> dict[str, Any]:
    data = load()
    current = dict(data.get(platform) or {})
    current.update({key: value for key, value in fields.items() if value is not None})
    current["saved_at"] = int(time.time())
    data[platform] = current
    save(data)
    return current


def forget(platform: str) -> bool:
    data = load()
    if platform not in data:
        return False
    data.pop(platform)
    save(data)
    return True


def configured(platform: str) -> tuple[bool, str]:
    """Is this platform ready to publish, and if not, what is missing?"""
    record = get(platform)
    if not record:
        return False, f"not connected — run: python -m app.bot.cli connect {platform}"
    # Only report an expired token when there is no way to renew it.
    if (
        record.get("expires_at")
        and not record.get("refresh_token")
        and int(record["expires_at"]) < int(time.time()) - 60
    ):
        return (
            False,
            f"the {platform} token expired — run: python -m app.bot.cli connect {platform}",
        )
    return True, ""
