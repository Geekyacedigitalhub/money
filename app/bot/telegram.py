"""Telegram 适配器（长轮询，无需公网 webhook）。

只需要一个 @BotFather 生成的 token：

    [bot]
    telegram_token = "123456:ABC..."

然后 ``python -m app.bot.cli serve`` 里带 ``--telegram``，或直接
``python -m app.bot.telegram``。

使用长轮询而不是 webhook 的原因是：它能在笔记本、家庭宽带、甚至没有公网 IP
的机器上直接跑，不需要 HTTPS 证书或反向代理。
"""

from __future__ import annotations

import os
import threading
import time
from typing import Any

import requests
from loguru import logger

from app.bot.config import BotSettings, load_settings
from app.bot.core import build_core
from app.bot.models import ChatMessage, Reply

API_BASE = "https://api.telegram.org"
_TEXT_LIMIT = 4000
_CAPTION_LIMIT = 1000


class TelegramBot:
    def __init__(self, settings: BotSettings | None = None) -> None:
        self.settings = settings or load_settings()
        self.token = (self.settings.telegram_token or "").strip()
        if not self.token:
            raise RuntimeError(
                "telegram_token is not configured. Set [bot] telegram_token in "
                "config.toml or export MPT_BOT_TELEGRAM_TOKEN."
            )
        self.core = build_core(self.settings)
        self.core.set_notifier(self._push_reply)
        self._stop = threading.Event()
        self._session = requests.Session()

    # ------------------------------------------------------------------
    # Telegram API helpers
    # ------------------------------------------------------------------

    def _url(self, method: str) -> str:
        return f"{API_BASE}/bot{self.token}/{method}"

    def _call(self, method: str, **payload: Any) -> dict:
        try:
            response = self._session.post(self._url(method), json=payload, timeout=65)
            body = response.json()
        except requests.RequestException as exc:
            logger.warning(f"telegram {method} failed: {exc}")
            return {"ok": False, "description": str(exc)}
        if not body.get("ok"):
            logger.warning(f"telegram {method} rejected: {body.get('description')}")
        return body

    def _download(self, file_id: str, suggested_name: str = "video.mp4") -> str | None:
        info = self._call("getFile", file_id=file_id)
        if not info.get("ok"):
            return None
        file_path = info.get("result", {}).get("file_path")
        if not file_path:
            return None

        from app.utils import utils

        target_dir = os.path.join(utils.storage_dir("bot", create=True), "uploads")
        os.makedirs(target_dir, exist_ok=True)
        suffix = os.path.splitext(file_path)[1] or os.path.splitext(suggested_name)[1] or ".mp4"
        target = os.path.join(target_dir, f"tg-{file_id[:16]}{suffix}")
        try:
            with self._session.get(
                f"{API_BASE}/file/bot{self.token}/{file_path}",
                stream=True,
                timeout=300,
            ) as response:
                response.raise_for_status()
                with open(target, "wb") as handle:
                    for chunk in response.iter_content(chunk_size=1024 * 512):
                        if chunk:
                            handle.write(chunk)
        except (requests.RequestException, OSError) as exc:
            logger.warning(f"telegram file download failed: {exc}")
            return None
        return target

    # ------------------------------------------------------------------
    # 发送
    # ------------------------------------------------------------------

    @staticmethod
    def _keyboard(reply: Reply) -> dict | None:
        if not reply.buttons:
            return None
        return {
            "inline_keyboard": [
                [{"text": button.label, "callback_data": button.data[:64]} for button in row]
                for row in reply.buttons
            ]
        }

    def _send_text(self, chat_id: str, reply: Reply) -> None:
        text = reply.text or "(no message)"
        if len(text) > _TEXT_LIMIT:
            text = text[:_TEXT_LIMIT] + "\n…"
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": True,
        }
        keyboard = self._keyboard(reply)
        if keyboard:
            payload["reply_markup"] = keyboard
        self._call("sendMessage", **payload)

    def _send_video(self, chat_id: str, reply: Reply) -> None:
        keyboard = self._keyboard(reply)
        caption = reply.text or ""
        if len(caption) > _CAPTION_LIMIT:
            caption = ""
        try:
            with open(reply.video or "", "rb") as handle:
                data: dict[str, Any] = {"chat_id": chat_id}
                if caption:
                    data["caption"] = caption
                    data["parse_mode"] = "Markdown"
                if keyboard:
                    import json

                    data["reply_markup"] = json.dumps(keyboard)
                response = self._session.post(
                    self._url("sendVideo"),
                    data=data,
                    files={"video": handle},
                    timeout=600,
                )
            if response.status_code != 200:
                logger.warning(f"telegram sendVideo failed: {response.text[:200]}")
                # 视频发送失败时至少把文字和按钮发出去，避免用户看不到下一步。
                self._send_text(chat_id, reply)
                return
            # 长文案在视频之后单独发送一次，保证按钮和说明都在。
            if len(reply.text or "") > _CAPTION_LIMIT:
                self._send_text(chat_id, reply)
        except OSError as exc:
            logger.warning(f"telegram cannot read video: {exc}")
            self._send_text(chat_id, reply)

    def send(self, chat_id: str, reply: Reply) -> None:
        if reply.video and os.path.isfile(reply.video):
            self._send_video(chat_id, reply)
        else:
            self._send_text(chat_id, reply)

    def _push_reply(self, chat_id: str, reply: Reply) -> None:
        """内核异步产出的回复通过这里推回 Telegram。"""
        self.send(chat_id, reply)

    # ------------------------------------------------------------------
    # 接收
    # ------------------------------------------------------------------

    def _handle_update(self, update: dict) -> None:
        try:
            if "callback_query" in update:
                query = update["callback_query"]
                chat_id = str(query.get("message", {}).get("chat", {}).get("id", ""))
                user = query.get("from", {})
                self._call("answerCallbackQuery", callback_query_id=query.get("id"))
                replies = self.core.handle(
                    ChatMessage(
                        chat_id=chat_id,
                        callback=str(query.get("data") or ""),
                        user_id=str(user.get("id", "")),
                        display_name=str(user.get("first_name") or ""),
                    )
                )
                for reply in replies:
                    self.send(chat_id, reply)
                return

            message = update.get("message") or update.get("edited_message") or {}
            if not message:
                return
            chat_id = str(message.get("chat", {}).get("id", ""))
            sender = message.get("from", {})
            attachment = None
            for key in ("video", "video_note", "document", "animation"):
                media = message.get(key)
                if isinstance(media, dict) and media.get("file_id"):
                    attachment = self._download(
                        media["file_id"], media.get("file_name") or "video.mp4"
                    )
                    break

            replies = self.core.handle(
                ChatMessage(
                    chat_id=chat_id,
                    text=str(message.get("text") or message.get("caption") or ""),
                    user_id=str(sender.get("id", "")),
                    display_name=str(sender.get("first_name") or ""),
                    attachment=attachment,
                )
            )
            for reply in replies:
                self.send(chat_id, reply)
        except Exception as exc:  # noqa: BLE001 - 单条消息失败不应终止轮询
            logger.exception(f"failed to handle telegram update: {exc}")

    def run(self) -> None:
        logger.info("telegram bot polling started")
        offset = 0
        while not self._stop.is_set():
            try:
                response = self._session.get(
                    self._url("getUpdates"),
                    params={
                        "offset": offset,
                        "timeout": 30,
                        "allowed_updates": '["message","edited_message","callback_query"]',
                    },
                    timeout=45,
                )
                body = response.json()
            except (requests.RequestException, ValueError) as exc:
                logger.warning(f"telegram polling error: {exc}")
                time.sleep(3)
                continue

            if not body.get("ok"):
                logger.warning(f"telegram getUpdates failed: {body.get('description')}")
                time.sleep(3)
                continue

            for update in body.get("result", []):
                offset = max(offset, int(update.get("update_id", 0)) + 1)
                self._handle_update(update)
        logger.info("telegram bot stopped")

    def stop(self) -> None:
        self._stop.set()


def main() -> int:
    logging_configured = False
    try:
        from app.utils.logging_utils import configure_logging  # type: ignore

        configure_logging()
        logging_configured = True
    except Exception:  # noqa: BLE001 - 该辅助函数并非所有版本都存在
        logging_configured = False
    if not logging_configured:
        logger.info("starting telegram bot")

    try:
        bot = TelegramBot()
    except RuntimeError as exc:
        logger.error(str(exc))
        return 2
    try:
        bot.run()
    except KeyboardInterrupt:
        bot.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
