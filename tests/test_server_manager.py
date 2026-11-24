import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from src.bot_screen import BotLogs
from src.config import Node
from src.server_manager import ServerManager
from src.ui import Dashboard


def test_start_and_attach_use_the_same_systemd_unit():
    node = Node("AdminVPS", "localhost", "root", "fixture", (), 22, None)
    manager = ServerManager(node)
    response = SimpleNamespace(stdout="active\nЗапись журнала\n")
    with patch("src.server_manager.asyncssh.connect") as connect:
        ssh = connect.return_value.__aenter__.return_value
        ssh.run = AsyncMock(return_value=response)

        asyncio.run(manager.change("start"))
        assert ssh.run.await_args.args[0] == "systemctl start vpn-manager.service"

        state, text = asyncio.run(manager.snapshot())
        assert state == "запущен"
        assert text == "Запись журнала\n"
        query = ssh.run.await_args.args[0]
        assert "vpn-manager.service" in query and "telegram.log" in query
        assert "systemctl start" not in query


def test_attach_kill_switch_stops_the_attached_manager(tmp_path):
    async def exercise():
        manager = SimpleNamespace(
            snapshot=AsyncMock(return_value=("запущен", "Запись журнала")),
            change=AsyncMock(),
        )
        app = Dashboard([], tmp_path / ".env", manager=manager)
        async with app.run_test() as pilot:
            assert isinstance(app.screen, BotLogs)
            manager.change.assert_not_awaited()
            await pilot.press("f12")

        manager.change.assert_awaited_once_with("stop")

    asyncio.run(exercise())
