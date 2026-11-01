"""MoneyPrinterTurbo social publishing bot.

前端无关的对话机器人内核。Telegram、网页聊天等适配器只负责把平台事件转换成
``ChatMessage``，再渲染 ``BotCore`` 返回的 ``Reply``，因此渠道与生成/发布逻辑
完全解耦。
"""

from app.bot.core import BotCore, build_core

__all__ = ["BotCore", "build_core"]
