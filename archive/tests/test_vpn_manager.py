import tempfile
import unittest
from contextlib import closing, redirect_stdout
from io import StringIO
from pathlib import Path
from ssl import SSLCertVerificationError
from unittest.mock import patch
from urllib.error import URLError

from vpn_manager import providers
from vpn_manager.__main__ import execute, parser
from vpn_manager.console.tui import Console, printable
from vpn_manager.http import APIError, NoRedirect, transport_error
from vpn_manager.messaging import alerts
from vpn_manager.monitoring import monitor, probes


class MonitorTests(unittest.TestCase):
    def test_threshold_recovery_and_independent_delivery(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            closing(monitor.open_state(Path(directory) / "state.sqlite3")) as connection,
        ):
            channels = ["telegram", "max"]
            clock = "2026-11-23T12:00:00+00:00"
            failed = [("node-tunnel", False, "no HTTPS")]

            for _ in range(2):
                monitor.record(connection, "lte", 3, failed, channels, clock)

            self.assertEqual(connection.execute("SELECT count(*) FROM outbox").fetchone()[0], 0)

            for _ in range(2):
                monitor.record(connection, "lte", 3, failed, channels, clock)

            monitor.record(
                connection, "lte", 3, [("node-tunnel", True, "HTTP 204")], channels, clock
            )
            self.assertEqual(connection.execute("SELECT count(*) FROM outbox").fetchone()[0], 4)
            sent = []

            def send(channel, text):
                if channel == "telegram":
                    raise APIError("HTTP 503")

                sent.append(text.split()[0])

            with patch.object(alerts, "send", side_effect=send), patch.object(monitor, "sleep"):
                self.assertTrue(monitor.deliver(connection, channels))

            self.assertEqual(sent, ["Сбой", "Восстановлено"])
            self.assertEqual(
                connection.execute("SELECT DISTINCT channel FROM outbox").fetchall(),
                [("telegram",)],
            )

            with patch.object(alerts, "send"):
                self.assertFalse(monitor.deliver(connection, channels))

            self.assertEqual(connection.execute("SELECT count(*) FROM outbox").fetchone()[0], 0)

    @patch("vpn_manager.monitoring.probes.subprocess.run")
    def test_tunnel_uses_loopback_socks_with_remote_dns(self, run):
        run.return_value.returncode = 0
        run.return_value.stdout = "204"
        result = monitor.probe_check(
            probes.Check(
                "node-tunnel",
                "tunnel",
                url="https://example.com/health",
                proxy_port=10808,
            )
        )
        self.assertEqual(result, (True, "HTTP 204"))
        self.assertIn("socks5h://127.0.0.1:10808", run.call_args.args[0])


class ProviderTests(unittest.TestCase):
    @patch("vpn_manager.providers.api")
    def test_power_preview_makes_no_request(self, api):
        args = parser().parse_args(["onedash", "power", "123", "reboot"])
        self.assertTrue(providers.onedash(args)["preview"])
        api.assert_not_called()
        args = parser().parse_args(["onedash", "power", "123", "reboot", "--apply"])
        providers.onedash(args)
        api.assert_called_once_with("onedash", "/vps/123/power", "POST", {"action": "reboot"})

    @patch("vpn_manager.providers.api", return_value={"id": 123, "credentials": "hidden"})
    def test_fornex_ticket_payload(self, api):
        with tempfile.TemporaryDirectory() as directory:
            body = Path(directory) / "ticket.txt"
            body.write_text("Тестовый черновик", encoding="utf-8")
            arguments = [
                "fornex",
                "ticket",
                "--order",
                "34-123",
                "--subject",
                "IPv4",
                "--body-file",
                str(body),
            ]
            self.assertTrue(providers.fornex(parser().parse_args(arguments))["preview"])
            api.assert_not_called()
            result = providers.fornex(parser().parse_args([*arguments, "--send"]))
            self.assertEqual(result, {"submitted": True, "id": 123})
            api.assert_called_once_with(
                "fornex",
                "/tickets/ticket/",
                "POST",
                {
                    "subject": "IPv4",
                    "message": "Тестовый черновик",
                    "order": "34-123",
                },
            )

    @patch.dict("os.environ", {"FORNEX_API_KEY": "fixture", "ONEDASH_API_KEY": "fixture"})
    @patch("vpn_manager.providers.request", return_value={"type": True})
    def test_provider_commands_address_the_expected_api_endpoints(self, request):
        scenarios = [
            ("fornex orders --page 2", "GET", "/orders/vps/?page=2&page_size=25", None),
            ("fornex order 34-42", "GET", "/orders/vps/34-42/", None),
            ("fornex power-status 34-42", "GET", "/vps/34-42/status/", None),
            ("fornex power 34-42 start --apply", "POST", "/vps/34-42/start/", {}),
            ("fornex power 34-42 reboot --apply", "POST", "/vps/34-42/restart/", {}),
            ("onedash balance", "GET", "/balance", None),
            ("onedash orders", "GET", "/orders", None),
            ("onedash order 42", "POST", "/orders/info", {"order_id": 42}),
            ("onedash vps --page 2", "GET", "/vps?page=2&per_page=25", None),
            ("onedash ip-options 42", "GET", "/vps/42/ips", None),
            ("onedash power-status 42", "GET", "/vps/42/power", None),
            ("onedash power 42 start --apply", "POST", "/vps/42/power", {"action": "start"}),
            ("onedash power 42 reboot --apply", "POST", "/vps/42/power", {"action": "reboot"}),
            ("onedash power 42 shutdown --apply", "POST", "/vps/42/power", {"action": "shutdown"}),
        ]

        for command, method, path, body in scenarios:
            with self.subTest(command=command):
                args = parser().parse_args(command.split())
                code, _ = execute(args)
                self.assertEqual(code, 0)
                base, auth = (
                    ("https://ru.fornex.com/api", "Api-Key fixture")
                    if args.group == "fornex"
                    else ("https://api.rdp-onedash.ru/api", "Bearer fixture")
                )
                request.assert_called_with(
                    base + path, method=method, body=body, headers={"Authorization": auth}
                )

    @patch("vpn_manager.providers.subprocess.run")
    def test_adminvps_override_uses_pinned_host_and_returns_ssh_status(self, run):
        with tempfile.TemporaryDirectory() as directory:
            pin = Path(directory) / "known_hosts"
            pin.touch()

            with patch.dict(
                "os.environ",
                {
                    "ADMINVPS_SSH_HOST": "203.0.113.12",
                    "ADMINVPS_SSH_KNOWN_HOSTS": str(pin),
                },
            ):
                run.return_value.returncode = 3
                code, result = execute(parser().parse_args(["adminvps", "ssh-status"]))

        self.assertEqual((code, result), (3, None))
        command = run.call_args.args[0]
        self.assertIn("StrictHostKeyChecking=yes", command)
        self.assertIn("HostName=203.0.113.12", command)
        self.assertIn(f'UserKnownHostsFile="{pin.resolve()}"', command)
        self.assertIn('exit "$service_status"', command[-1])

    def test_tls_failure_and_redirect_do_not_expose_credentials(self):
        error = URLError(SSLCertVerificationError("secret"))
        self.assertIn("TLS", transport_error(error))
        self.assertNotIn("secret", transport_error(error))
        self.assertIsNone(
            NoRedirect().redirect_request(None, None, 302, "", {}, "https://other.test")
        )


class AlertTests(unittest.TestCase):
    @patch.dict(
        "os.environ",
        {
            "MAX_BOT_TOKEN": "fixture.test",
            "MAX_CHAT_ID": "-123",
            "TELEGRAM_BOT_TOKEN": "123:fixture",
            "TELEGRAM_CHAT_ID": "-456",
        },
        clear=True,
    )
    @patch("vpn_manager.messaging.telegram.Bot")
    @patch("vpn_manager.messaging.max.Bot")
    def test_platforms_use_sdk(self, max_bot, telegram):
        from unittest.mock import AsyncMock

        max_bot.return_value.send_message = AsyncMock(return_value=object())
        max_bot.return_value.close_session = AsyncMock()
        client = telegram.return_value.__aenter__.return_value
        self.assertEqual(alerts.configured_channels(), ["telegram", "max"])
        alerts.send("max", "sample")
        alerts.send("telegram", "sample")
        max_bot.return_value.send_message.assert_awaited_once_with(
            chat_id=-123,
            user_id=None,
            text="sample",
            disable_link_preview=True,
        )
        max_bot.return_value.close_session.assert_awaited_once()
        telegram.assert_called_once_with("123:fixture")
        client.send_message.assert_awaited_once_with(-456, "sample")


class ConsoleTests(unittest.TestCase):
    def console(self):
        return Console(parser(), execute, Path("state.sqlite3"), Path("monitor.toml"))

    @patch("vpn_manager.providers.api")
    @patch("builtins.input", side_effect=["4", "0", "0"])
    def test_navigation_does_not_attempt_network(self, prompt, api):
        output = StringIO()

        with redirect_stdout(output):
            self.assertEqual(self.console().run(), 0)

        self.assertIn("Главное меню", output.getvalue())
        self.assertIn("Список VPS", output.getvalue())
        api.assert_not_called()

    @patch("vpn_manager.providers.api", return_value={"type": True})
    @patch("vpn_manager.console.tui.sys.stdin.isatty", return_value=True)
    @patch("builtins.input", side_effect=["", "123"])
    def test_power_sends_only_after_confirming_exact_vps(self, prompt, isatty, api):
        with redirect_stdout(StringIO()):
            self.console().power("onedash", 123, "reboot", "Перезапуск")
            api.assert_not_called()
            self.console().power("onedash", 123, "reboot", "Перезапуск")

        api.assert_called_once_with("onedash", "/vps/123/power", "POST", {"action": "reboot"})

    @patch("vpn_manager.providers.api")
    @patch("vpn_manager.console.tui.sys.stdin.isatty", return_value=False)
    def test_piped_ui_cannot_confirm_mutations(self, isatty, api):
        with redirect_stdout(StringIO()):
            self.console().power("onedash", 123, "reboot", "Перезапуск")

        api.assert_not_called()

    @patch("vpn_manager.providers.api", return_value={"id": 42})
    @patch("vpn_manager.console.tui.sys.stdin.isatty", return_value=True)
    def test_ticket_sends_the_previewed_text(self, isatty, api):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ticket.txt"
            path.write_text("Исходный текст", encoding="utf-8")

            def confirm(question, phrase):
                path.write_text("Файл изменился", encoding="utf-8")

                return True

            console = self.console()

            with (
                redirect_stdout(StringIO()),
                patch.object(console, "confirm", side_effect=confirm),
                patch("builtins.input", side_effect=["Проверка линии", str(path)]),
            ):
                console.ticket("fornex", "34-123")

        sent = api.call_args.args[3]
        self.assertEqual(
            sent,
            {
                "subject": "Проверка линии",
                "message": "Исходный текст",
                "order": "34-123",
            },
        )

    def test_service_labels_cannot_inject_terminal_sequences(self):
        self.assertNotIn("\x1b", printable("name\x1b[2J"))

    @patch("vpn_manager.providers.api")
    @patch("builtins.input", side_effect=["n", "1", "p", "0"])
    def test_provider_list_selects_resource_from_current_page(self, prompt, api):
        api.side_effect = [
            {"data": {"items": [{"id": identifier}], "pagination": {"pages": 2}}}
            for identifier in (101, 202, 101)
        ]
        console = self.console()

        with redirect_stdout(StringIO()), patch.object(console, "resource") as resource:
            console.browse("onedash", "vps")

        resource.assert_called_once_with("onedash", "vps", 202)
        self.assertEqual(
            [call.args[1] for call in api.call_args_list],
            [
                "/vps?page=1&per_page=25",
                "/vps?page=2&per_page=25",
                "/vps?page=1&per_page=25",
            ],
        )
