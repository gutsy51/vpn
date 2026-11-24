import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

from textual.widgets import OptionList, Static, TextArea

from src.config import Node
from src.ui import Dashboard


def test_keyboard_navigation_and_logs():
    async def exercise():
        node = Node("test", "host", "user", "secret", ("x-ui", "xray"), 22, Path("hosts"))
        app = Dashboard([node])
        fetch = AsyncMock(return_value="Запись журнала")
        with patch("src.ui.inspect_service", fetch):
            async with app.run_test(size=(80, 24)) as pilot:
                await pilot.press("down", "3")
                await pilot.pause()
                fetch.assert_awaited_with(node, "xray", logs=True)
                log = app.query_one(TextArea)
                log.focus()
                with patch("shutil.which", return_value=None):
                    await pilot.press("f7", "ctrl+c")
                assert app.clipboard == "Запись журнала"
                app.query_one(OptionList).focus()
                assert "Обновлено" in str(app.query_one("#activity", Static).content)
                await pilot.press("tab", "2")
                await pilot.pause()
                fetch.assert_awaited_with(node, "xray", logs=False)
                assert app.query_one(OptionList).highlighted == 1
                await pilot.press("0")

    asyncio.run(exercise())


def test_add_server_from_empty_dashboard(tmp_path):
    from textual.widgets import Input

    from src.add_server import AddServer
    from src.config import inventory

    async def exercise():
        path = tmp_path / ".env"
        path.write_text("# настройки\nOTHER=value\n", encoding="utf-8")
        app = Dashboard([], path)
        fetch = AsyncMock(return_value="ActiveState=active\nSubState=running")

        with patch("src.ui.inspect_service", fetch):
            async with app.run_test(size=(90, 50)) as pilot:
                await pilot.press("5")
                assert isinstance(app.screen, AddServer)
                fields = {
                    "name": "Office",
                    "SSH_HOST": "localhost",
                    "SSH_USER": "dev",
                    "SSH_PASSWORD": "p'ass\\word",
                    "SERVICES": "first,second",
                }
                for key, value in fields.items():
                    app.screen.query_one("#" + key, Input).value = value

                await pilot.click("#save")
                await pilot.pause()
                assert app.results[0][0] == "active / running"
                assert app.results[1][0] == "active / running"
                assert inventory(path)[0].password == fields["SSH_PASSWORD"]
                assert "OTHER=value" in path.read_text(encoding="utf-8")
                await pilot.press("0")

    asyncio.run(exercise())


def test_telegram_log_screen(tmp_path):
    import logging

    from src.bot_log import logger, record_events, tail
    from src.bot_screen import BotLogs

    async def exercise():
        env = tmp_path / ".env"
        app = Dashboard([], env)

        async with app.run_test() as pilot:
            await pilot.press("6")
            assert isinstance(app.screen, BotLogs)
            assert "Журнала пока нет" in app.screen.content

            with record_events(env):
                logger.info("Запуск Telegram")
                logging.getLogger("aiogram.dispatcher").error("fixture-secret")

            await pilot.press("4")
            assert "Запуск Telegram" in app.screen.content
            with patch("shutil.which", return_value=None):
                await pilot.press("f7", "ctrl+c")
            assert "Запуск Telegram" in app.clipboard
            assert "fixture-secret" not in tail(env)
            await pilot.press("0")
            assert not isinstance(app.screen, BotLogs)

    asyncio.run(exercise())


def test_dashboard_menu_numbers_are_ordered():
    numbers = [binding.key for binding in Dashboard.BINDINGS if binding.key in "123456"]
    assert numbers == list("123456")


def test_telegram_log_copies_to_windows_clipboard(tmp_path):
    async def exercise():
        app = Dashboard([], tmp_path / ".env")
        async with app.run_test() as pilot:
            await pilot.press("6", "f8")
            copied = send.call_args.kwargs["input"].decode("utf-16")
            assert "Журнала пока нет" in copied
            log = app.screen.query_one(TextArea)
            log.move_cursor((0, 0))
            log.move_cursor((0, 7), select=True)
            await pilot.click(log, offset=(2, 1), button=3)
            assert send.call_args.kwargs["input"].decode("utf-16") == "Журнала"

    with patch("shutil.which", return_value="clip.exe"), patch("subprocess.run") as send:
        asyncio.run(exercise())
