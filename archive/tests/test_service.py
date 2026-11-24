import json
import os
from http.client import HTTPConnection
from pathlib import Path
from socketserver import BaseRequestHandler, TCPServer
from threading import Event, Thread
from time import time
from unittest.mock import Mock

import pytest
from aiogram.types import Message

from vpn_manager.config import Settings
from vpn_manager.containers.compose import ComposeError, ComposeManager
from vpn_manager.messaging.commands import ChatCommands
from vpn_manager.messaging.max import MaxWebhook, parse_max
from vpn_manager.messaging.models import ChatAddress, IncomingMessage, MonitorSummary, ProbeSummary
from vpn_manager.messaging.telegram import parse_telegram
from vpn_manager.monitoring.probes import Check, probe_check, read_socket


def test_settings_keep_file_values_out_of_process_environment(tmp_path, monkeypatch):
    monkeypatch.delenv("VPN_TEST_LITERAL", raising=False)
    path = tmp_path / ".env"
    literal = "$" + "{OTHER} # данные"
    path.write_text(f"VPN_TEST_LITERAL='{literal}'\n", encoding="utf-8")
    config = Settings()
    config.load(path)

    assert config.get("VPN_TEST_LITERAL") == literal
    assert "VPN_TEST_LITERAL" not in os.environ
    monkeypatch.setenv("VPN_TEST_LITERAL", "")
    assert config.get("VPN_TEST_LITERAL") == ""


def test_roles_use_platform_sender_not_chat_id():
    checks = Mock()
    checks.names.return_value = ("office",)
    checks.status.return_value = MonitorSummary((ProbeSummary("office", "доступен"),))
    checks.ping.return_value = True
    commands = ChatCommands(checks, frozenset({7}))
    group = ChatAddress("chat_id", 7)

    assert "администраторам" in (
        commands.reply(IncomingMessage(8, group, "/diagnostics"), "") or ""
    )
    assert "Очередь" in (commands.reply(IncomingMessage(7, group, "/diagnostics"), "") or "")
    assert commands.reply(IncomingMessage(8, group, "/ping office"), "") == "office - доступен"
    assert "Укажите имя" in (commands.reply(IncomingMessage(8, group, "/ping 127.0.0.1"), "") or "")
    assert commands.reply(IncomingMessage(8, group, "/status@other"), "ourbot") is None
    checks.ping.assert_called_once_with("office", include_private=False)
    assert commands.reply(IncomingMessage(7, group, "/checks"), "") == "/check office"
    checks.names.assert_called_with(include_private=True)


def test_platform_messages_preserve_the_chat_and_authentic_sender():
    telegram = {
        "update_id": 1,
        "message": {
            "message_id": 2,
            "date": 0,
            "from": {"id": 7, "is_bot": False, "first_name": "Test"},
            "chat": {"id": -42, "type": "group"},
            "text": "/status",
        },
    }
    max_update = {
        "update_type": "message_created",
        "message": {
            "sender": {"user_id": 7},
            "recipient": {"user_id": 999, "chat_type": "dialog"},
            "body": {"text": "/status"},
        },
    }
    assert parse_telegram(Message.model_validate(telegram["message"])) == IncomingMessage(
        7,
        ChatAddress("chat_id", -42),
        "/status",
    )
    assert parse_max(max_update) == IncomingMessage(
        7, ChatAddress("user_id", 7), "/status", private_chat=True
    )
    telegram["message"]["sender_chat"] = {"id": 7, "type": "group"}
    assert parse_telegram(Message.model_validate(telegram["message"])) is None


def test_max_webhook_checks_secret_before_accepting_a_group_command():
    incoming = {
        "update_type": "message_created",
        "timestamp": int(time() * 1000),
        "message": {
            "sender": {"user_id": 7},
            "recipient": {"chat_id": -42, "chat_type": "chat"},
            "body": {"text": "/status"},
        },
    }
    responder = Mock()
    handled = Event()
    responder.handle.side_effect = lambda _message: handled.set()
    server = MaxWebhook(0, "secret-for-local-check", responder)
    stop = Event()
    thread = Thread(target=server.run, args=(stop,))
    thread.start()
    port = server.server.server_port

    try:
        with pytest.MonkeyPatch.context() as monkeypatch:
            monkeypatch.delenv("HTTP_PROXY", raising=False)

            for key, expected in (("wrong", 403), ("secret-for-local-check", 200)):
                connection = HTTPConnection("127.0.0.1", port, timeout=3)

                try:
                    connection.request(
                        "POST",
                        MaxWebhook.path,
                        json.dumps(incoming),
                        {MaxWebhook.header: key, "Content-Type": "application/json"},
                    )
                    assert connection.getresponse().status == expected
                finally:
                    connection.close()

        assert handled.wait(3)
        assert responder.handle.call_count == 1
        assert responder.handle.call_args.args[0].destination == ChatAddress("chat_id", -42)
    finally:
        stop.set()
        thread.join(timeout=4)


def test_container_publish_restores_previous_config_when_start_fails(tmp_path: Path):
    manager = ComposeManager(tmp_path)
    manager.workspace.mkdir()
    manager.current.write_text("рабочая конфигурация", encoding="utf-8")
    candidate = manager.workspace / "candidate.json"
    candidate.write_text("новая конфигурация", encoding="utf-8")
    invoke = Mock(side_effect=[ComposeError("тестовый сбой"), None])
    manager.invoke = invoke

    with pytest.raises(ComposeError, match="тестовый сбой"):
        manager.publish(candidate)

    assert manager.current.read_text(encoding="utf-8") == "рабочая конфигурация"
    assert invoke.call_count == 2


def test_compose_keeps_mutation_scoped_to_the_named_service(tmp_path: Path):
    manager = ComposeManager(tmp_path)
    invoke = Mock()
    manager.invoke = invoke
    manager.manage("kill", "xray")
    invoke.assert_called_once_with("kill", "xray")

    with pytest.raises(ValueError):
        manager.manage("kill", "--volumes")


def test_tcp_probe_asks_the_socks_server_to_connect_to_the_named_target():
    conversations: list[bytes] = []

    class SocksHandler(BaseRequestHandler):
        def handle(self) -> None:
            conversations.append(read_socket(self.request, 3))
            self.request.sendall(b"\x05\x00")
            header = read_socket(self.request, 5)
            conversations.append(header + read_socket(self.request, header[4] + 2))
            self.request.sendall(b"\x05\x00\x00\x01\x7f\x00\x00\x01\x00\x00")

    with TCPServer(("127.0.0.1", 0), SocksHandler) as server:
        worker = Thread(target=server.handle_request, daemon=True)
        worker.start()
        check = Check(
            "peer-ru", "tcp", host="peer.example", port=443, proxy_port=server.server_address[1]
        )
        reachable, _ = probe_check(check)
        worker.join(timeout=3)

    assert reachable
    assert conversations == [b"\x05\x01\x00", b"\x05\x01\x00\x03\x0cpeer.example\x01\xbb"]


def test_panel_command_uses_configured_https_check_and_sender_role(tmp_path, monkeypatch):
    from vpn_manager.monitoring.monitor import StoredChecks

    config = tmp_path / "monitor.toml"
    config.write_text(
        'probe = "local"\n[[checks]]\nname = "panel"\nkind = "https"\n'
        'url = "https://panel.example.test/"\nstatus = 200\n',
        encoding="utf-8",
    )
    monkeypatch.setattr("vpn_manager.monitoring.probes.shutil.which", lambda _: "curl")
    request = Mock(return_value=Mock(returncode=0, stdout="200"))
    monkeypatch.setattr("vpn_manager.monitoring.probes.subprocess.run", request)
    commands = ChatCommands(StoredChecks(config, tmp_path / "state.db", 180), frozenset({1}))
    group = ChatAddress("chat_id", -5)

    assert "panel" not in commands.reply(IncomingMessage(2, group, "/checks"), "")
    assert "Укажите имя" in commands.reply(IncomingMessage(2, group, "/check panel"), "")
    request.assert_not_called()
    assert commands.reply(IncomingMessage(1, group, "/checks"), "") == "/check panel"
    assert commands.reply(IncomingMessage(1, group, "/check panel"), "") == "panel - доступен"
    assert request.call_args.args[0][-1] == "https://panel.example.test/"


def test_logs_require_private_admin_and_known_service(monkeypatch):
    read = Mock(return_value="журнал")
    monkeypatch.setattr("vpn_manager.messaging.commands.service_logs", read)
    commands = ChatCommands(Mock(), frozenset({7}))

    def reply(sender, private, command):
        return commands.reply(
            IncomingMessage(sender, ChatAddress("chat_id", 8), command, private_chat=private), ""
        )

    assert "администраторам" in reply(8, True, "/logs xray")
    assert "личный чат" in reply(7, False, "/logs xray")
    assert "Укажите сервис" in reply(7, True, "/logs --help")
    read.assert_not_called()
    assert reply(7, True, "/logs x-ui") == "журнал"
    read.assert_called_once_with("x-ui")


@pytest.mark.parametrize("service, executable", [("x-ui", "ssh"), ("xray", "docker")])
def test_logs_cli_uses_bounded_read(service, executable, monkeypatch, capsys):
    from vpn_manager.__main__ import execute
    from vpn_manager.console.arguments import parser

    run = Mock(return_value=Mock(returncode=0, stdout="событие\n"))
    monkeypatch.setattr("vpn_manager.logs.subprocess.run", run)
    monkeypatch.setenv("ADMINVPS_SSH_HOST", "")
    monkeypatch.setenv("ADMINVPS_SSH_KNOWN_HOSTS", "")
    assert execute(parser().parse_args(["logs", service])) == (0, None)
    assert capsys.readouterr().out == "событие\n"
    assert run.call_args.args[0][0] == executable
    assert "100" in " ".join(run.call_args.args[0])
    assert run.call_args.kwargs["timeout"] == 15
