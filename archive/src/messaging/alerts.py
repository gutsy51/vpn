import asyncio
import re

from vpn_manager.config import settings

from .max import send_max_reply
from .models import ChatAddress
from .settings import bot_token
from .telegram import send_alert


def configured_channels() -> list[str]:
    channels = []

    for channel in ("telegram", "max"):
        chat = settings.get(f"{channel.upper()}_CHAT_ID")

        if not chat:
            continue

        bot_token(channel)

        if not re.fullmatch(r"-?[0-9]{1,19}", chat):
            raise ValueError(f"Некорректный ID чата {channel}")

        channels.append(channel)

    return channels


def send(channel: str, text: str) -> None:
    if channel not in ("telegram", "max"):
        raise ValueError("Неизвестный канал доставки")

    address = ChatAddress("chat_id", int(settings.get(f"{channel.upper()}_CHAT_ID")))

    if channel == "telegram":
        asyncio.run(send_alert(bot_token(channel), address, text))
    else:
        send_max_reply(address, text)
