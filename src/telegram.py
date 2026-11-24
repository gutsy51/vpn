import asyncio
import os
from contextlib import suppress
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from urllib.parse import urlsplit

from aiogram import Bot, Dispatcher, Router
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.session.middlewares.base import NextRequestMiddlewareType
from aiogram.exceptions import TelegramNetworkError, TelegramRetryAfter
from aiogram.filters import Command
from aiogram.methods import GetMe, Response, TelegramMethod
from aiogram.methods.base import TelegramType
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from dotenv import dotenv_values

from .bot_log import logger, record_events
from .config import Node
from .services import inspect_service


async def retry_telegram_request(
    make_request: NextRequestMiddlewareType[TelegramType],
    bot: Bot,
    method: TelegramMethod[TelegramType],
) -> Response[TelegramType]:
    network_delay = 2
    while True:
        try:
            result = await make_request(bot, method)
        except TelegramRetryAfter as error:
            logger.warning(
                "Лимит запросов Telegram (%s). Повтор через %s с",
                type(method).__name__,
                error.retry_after,
            )
            await asyncio.sleep(error.retry_after)
        except TelegramNetworkError as error:
            if method.__api_method__ != GetMe.__api_method__:
                raise

            original = getattr(error, "original", None)
            logger.warning(
                "Сбой сети Telegram (getMe, %s). Повтор через %s с",
                type(original or error).__name__,
                network_delay,
            )
            await asyncio.sleep(network_delay)
            network_delay = min(network_delay * 2, 30)
        else:
            if method.__api_method__ == GetMe.__api_method__:
                logger.info("Telegram подтвердил подключение бота")

            return result


def proxy_url(enabled: str | None, value: str | None) -> str | None:
    flag = (enabled or "").strip().lower()
    if flag in {"", "0", "false"}:
        return None

    if flag not in {"1", "true"}:
        raise ValueError("Укажите 0 или 1 в TELEGRAM_PROXY_ENABLED")

    address = (value or "").strip()
    try:
        parsed = urlsplit(address)
        if (
            parsed.scheme not in {"http", "socks4", "socks5"}
            or not parsed.hostname
            or not parsed.port
            or any(char.isspace() for char in address)
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError
    except ValueError:
        raise ValueError(
            "Укажите адрес в TELEGRAM_PROXY_URL, например http://127.0.0.1:10808"
        ) from None

    return address


def user_ids(value: str) -> set[int]:
    ids = set()

    for item in value.split(","):
        item = item.strip()

        if item:
            if not item.isascii() or not item.isdecimal() or int(item) < 1:
                raise ValueError("ID Telegram должны быть положительными числами через запятую")

            ids.add(int(item))

    return ids


def keyboard(buttons: list[tuple[str, str]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=label, callback_data=data)] for label, data in buttons
        ]
    )


class TelegramMenu:
    def __init__(self, nodes: list[Node], admins: set[int], users: set[int]):
        self.admins = admins
        self.allowed = users | admins
        self.targets: dict[str, tuple[Node, str]] = {}
        self.results: dict[str, str] = {}

        for node in nodes:
            for unit in node.services:
                identifier = sha256(f"{node.name}/{unit}".encode()).hexdigest()[:24]
                self.targets[identifier] = node, unit

    def menu(self) -> InlineKeyboardMarkup:
        return keyboard(
            [(f"{node.name} / {unit}", f"card:{key}") for key, (node, unit) in self.targets.items()]
        )

    async def start(self, message: Message) -> None:
        if (
            message.from_user is None
            or message.from_user.id not in self.allowed
            or message.chat.type != "private"
        ):
            logger.warning("Отклонён запрос к меню")
            await message.answer("Доступ закрыт.")
            return

        logger.info("Открыт список служб")
        await message.answer(
            "Выберите службу." if self.targets else "Узлов пока нет.", reply_markup=self.menu()
        )

    async def action(self, event: CallbackQuery) -> None:
        message = event.message
        sender = event.from_user.id
        if (
            sender not in self.allowed
            or not isinstance(message, Message)
            or message.chat.type != "private"
        ):
            await event.answer("Доступ закрыт.", show_alert=True)
            return

        action, _, key = (event.data or "").partition(":")
        if action == "menu":
            await event.answer()
            await message.answer("Выберите службу.", reply_markup=self.menu())
            return

        if key not in self.targets or action not in {"card", "check", "logs"}:
            await event.answer("Откройте актуальный список через /status.", show_alert=True)
            return

        admin = sender in self.admins
        if action != "card" and not admin:
            await event.answer("Требуются права администратора.", show_alert=True)
            return

        await event.answer()
        node, unit = self.targets[key]
        logger.info("Действие %s: %s / %s", action, node.name, unit)
        if action == "logs":
            try:
                text = await inspect_service(node, unit, logs=True)
            except ValueError as error:
                logger.warning("Чтение журнала: %s", error)
                text = str(error)
        else:
            if action == "check":
                await self.check(key)

            text = self.results.get(key, "Проверка ещё не выполнялась.")

        buttons = []
        if admin:
            buttons.extend([("Проверить сейчас", f"check:{key}"), ("Журнал", f"logs:{key}")])

        buttons.append(("К списку", "menu"))
        await message.answer(
            f"{node.name} / {unit}\n{text[-1800:]}", reply_markup=keyboard(buttons)
        )

    async def check(self, key: str) -> None:
        node, unit = self.targets[key]
        try:
            status = await inspect_service(node, unit, logs=False)
        except ValueError as error:
            logger.warning("Проверка %s / %s: %s", node.name, unit, error)
            status = str(error)

        self.results[key] = f"{datetime.now().astimezone():%d.%m %H:%M:%S %Z}\n{status}"

    async def check_all(self) -> None:
        limit = asyncio.Semaphore(6)

        async def probe(key: str) -> None:
            async with limit:
                try:
                    await self.check(key)
                except Exception as error:
                    logger.error("Фоновая проверка прервана: %s", type(error).__name__)

        await asyncio.gather(*(probe(key) for key in self.targets))

    def router(self) -> Router:
        router = Router()
        router.message.register(self.start, Command("start", "status", "help"))
        router.callback_query.register(self.action)

        return router


async def serve(nodes: list[Node], env_file: Path) -> None:
    with record_events(env_file):
        logger.info("Запуск Telegram")
        try:
            await poll(nodes, env_file)
        except Exception as error:
            logger.error("Работа прервана: %s", type(error).__name__)
            raise
        finally:
            logger.info("Telegram остановлен")


async def poll(nodes: list[Node], env_file: Path) -> None:
    values = {**dotenv_values(env_file, interpolate=False), **os.environ}
    token = values.get("TELEGRAM_BOT_TOKEN")
    if not token:
        logger.error("Отсутствует TELEGRAM_BOT_TOKEN")
        raise ValueError("Заполните TELEGRAM_BOT_TOKEN")

    proxy = proxy_url(values.get("TELEGRAM_PROXY_ENABLED"), values.get("TELEGRAM_PROXY_URL"))
    menu = TelegramMenu(
        nodes,
        user_ids(values.get("TELEGRAM_ADMIN_IDS") or ""),
        user_ids(values.get("TELEGRAM_USER_IDS") or ""),
    )
    dispatcher = Dispatcher()
    dispatcher.include_router(menu.router())

    session = AiohttpSession(proxy=proxy)
    session.middleware(retry_telegram_request)
    async with Bot(token, session=session) as bot:
        if proxy:
            logger.info("Прокси Telegram включён")

        checks = asyncio.create_task(menu.check_all())
        try:
            logger.info("Подключение Telegram к Bot API")
            await dispatcher.start_polling(bot, close_bot_session=False, tasks_concurrency_limit=8)
        finally:
            checks.cancel()
            with suppress(asyncio.CancelledError):
                await checks
