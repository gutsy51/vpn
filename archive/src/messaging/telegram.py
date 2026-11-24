import asyncio
from time import time

from aiogram import Bot, Dispatcher
from aiogram.exceptions import TelegramAPIError
from aiogram.types import Message

from vpn_manager.http import APIError

from .commands import ChatCommands
from .models import ChatAddress, IncomingMessage


def parse_telegram(message: Message) -> IncomingMessage | None:
    sender = message.from_user

    if not message.text or message.sender_chat or sender is None or sender.is_bot:
        return None

    return IncomingMessage(
        sender.id,
        ChatAddress("chat_id", message.chat.id),
        message.text,
        int(message.date.timestamp()),
        private_chat=message.chat.type == "private",
    )


async def send_alert(token: str, address: ChatAddress, text: str) -> None:
    try:
        async with Bot(token) as bot:
            await bot.send_message(address.identifier, text)
    except TelegramAPIError:
        raise APIError("Telegram отклонил отправку") from None


async def poll(token: str, commands: ChatCommands) -> None:
    dispatcher = Dispatcher()

    async with Bot(token) as bot:

        async def handle(message: Message) -> None:
            incoming = parse_telegram(message)

            if incoming is None or time() - incoming.timestamp > 300:
                return

            identity = await bot.me()
            reply = await asyncio.to_thread(commands.reply, incoming, identity.username or "")

            if reply:
                await message.answer(reply)

        dispatcher.message.register(handle)
        await dispatcher.start_polling(
            bot, handle_signals=False, handle_as_tasks=False, close_bot_session=False
        )
