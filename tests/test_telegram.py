import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from aiogram import Bot, Dispatcher
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.exceptions import TelegramNetworkError, TelegramRetryAfter
from aiogram.methods import AnswerCallbackQuery, GetMe, SendMessage
from aiogram.types import Update, User

from src.config import Node
from src.telegram import TelegramMenu, poll


def test_telegram_roles_and_service_buttons():
    async def exercise():
        node = Node("test", "localhost", "dev", "fixture", ("x-ui",), 22, None)
        menu = TelegramMenu([node], {7}, {8})
        key = next(iter(menu.targets))
        dispatcher = Dispatcher()
        dispatcher.include_router(menu.router())
        bot = Bot("123:fixture")
        send = AsyncMock(return_value=True)

        async def update(sender, action=None, group=False):
            user = {"id": sender, "is_bot": False, "first_name": "Test"}
            chat = {"id": -42 if group else sender, "type": "group" if group else "private"}
            message = {"message_id": 1, "date": 1, "chat": chat, "from": user, "text": "/status"}
            payload = (
                {"message": message}
                if action is None
                else {
                    "callback_query": {
                        "from": user,
                        "message": message,
                        "id": "id",
                        "chat_instance": "test",
                        "data": f"{action}:{key}",
                    }
                }
            )
            await dispatcher.feed_update(bot, Update.model_validate({"update_id": 1, **payload}))
            return send.await_args.args[1]

        with (
            patch.object(bot.session, "make_request", send),
            patch("src.telegram.inspect_service", AsyncMock(return_value="результат")) as inspect,
        ):
            reply = await update(99)
            assert isinstance(reply, SendMessage) and "закрыт" in reply.text
            reply = await update(8)
            assert reply.reply_markup.inline_keyboard[0][0].callback_data == f"card:{key}"
            await update(8, "card")
            assert isinstance(await update(8, "check"), AnswerCallbackQuery)
            assert isinstance(await update(7, "logs", group=True), AnswerCallbackQuery)
            inspect.assert_not_awaited()
            assert "результат" in (await update(7, "check")).text
            assert "результат" in (await update(8, "card")).text
            assert "результат" in (await update(7, "logs")).text
            inspect.assert_awaited_with(node, "x-ui", logs=True)

        await bot.session.close()

    asyncio.run(exercise())


@pytest.mark.parametrize(
    ("enabled", "override", "expected_proxy"),
    [("1", None, "http://127.0.0.1:10808"), ("1", "0", None), (None, None, None)],
)
def test_poll_uses_configured_proxy(tmp_path, monkeypatch, enabled, override, expected_proxy):
    for key in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_PROXY_ENABLED", "TELEGRAM_PROXY_URL"):
        monkeypatch.delenv(key, raising=False)

    env_file = tmp_path / ".env"
    config = "TELEGRAM_BOT_TOKEN=123:fixture\nTELEGRAM_PROXY_URL=http://127.0.0.1:10808\n"
    if enabled is not None:
        config += f"TELEGRAM_PROXY_ENABLED={enabled}\n"

    env_file.write_text(config, encoding="utf-8")
    if override is not None:
        monkeypatch.setenv("TELEGRAM_PROXY_ENABLED", override)

    with patch.object(Dispatcher, "start_polling", new_callable=AsyncMock) as start_polling:
        asyncio.run(poll([], env_file))

    bot = start_polling.await_args.args[0]
    assert bot.session.proxy == expected_proxy


@pytest.mark.parametrize("url", ["", "http://127.0.0.1", "http://127.0.0.1:99999"])
def test_poll_rejects_invalid_proxy(tmp_path, monkeypatch, url):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:fixture")
    monkeypatch.setenv("TELEGRAM_PROXY_ENABLED", "1")
    monkeypatch.setenv("TELEGRAM_PROXY_URL", url)

    with (
        patch.object(Dispatcher, "start_polling", new_callable=AsyncMock) as start_polling,
        pytest.raises(ValueError, match="TELEGRAM_PROXY_URL"),
    ):
        asyncio.run(poll([], tmp_path / ".env"))

    start_polling.assert_not_awaited()


def test_poll_starts_while_ssh_is_pending(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:fixture")
    monkeypatch.setenv("TELEGRAM_PROXY_ENABLED", "0")
    node = Node("test", "localhost", "dev", "fixture", ("x-ui",), 22, None)

    async def exercise():
        started = asyncio.Event()
        cancelled = asyncio.Event()

        async def inspect(*args, **kwargs):
            started.set()
            try:
                await asyncio.Future()
            finally:
                cancelled.set()

        async def receive(*args, **kwargs):
            await started.wait()
            assert not cancelled.is_set()

        with (
            patch("src.telegram.inspect_service", inspect),
            patch.object(Dispatcher, "start_polling", side_effect=receive),
        ):
            await asyncio.wait_for(poll([node], tmp_path / ".env"), timeout=1)

        assert cancelled.is_set()

    asyncio.run(exercise())


def test_poll_retries_rate_limit_and_network_error_in_get_me(tmp_path, monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:fixture")
    monkeypatch.setenv("TELEGRAM_PROXY_ENABLED", "0")
    identity = User(id=123, is_bot=True, first_name="Test")
    failure = TelegramRetryAfter(GetMe(), "fixture", retry_after=1)
    network_error = TelegramNetworkError(GetMe(), "fixture")

    async def receive(bot, **kwargs):
        assert await bot.me() == identity

    wait = AsyncMock()
    with (
        patch.object(Dispatcher, "start_polling", side_effect=receive),
        patch.object(
            AiohttpSession, "make_request", side_effect=[failure, network_error, identity]
        ) as request,
        patch("src.telegram.asyncio.sleep", wait),
    ):
        asyncio.run(poll([], tmp_path / ".env"))

    assert [call.args[0] for call in wait.await_args_list] == [1, 2]
    assert request.await_count == 3
