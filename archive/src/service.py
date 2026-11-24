import asyncio
import signal
import sqlite3
from pathlib import Path
from threading import Event

import portalocker

from vpn_manager.config import settings
from vpn_manager.messaging import alerts
from vpn_manager.messaging.commands import ChatCommands
from vpn_manager.messaging.max import ChatResponder, MaxWebhook, send_max_reply
from vpn_manager.messaging.settings import bot_token, parse_admin_ids
from vpn_manager.messaging.telegram import poll
from vpn_manager.monitoring import monitor


class ManagerService:
    def __init__(self, config: Path, state: Path, interval: int):
        if interval < 15:
            raise ValueError("Интервал проверок должен быть не меньше 15 секунд")

        self.config = config
        self.state = state
        self.interval = interval
        self.stop = Event()
        self.checks = monitor.StoredChecks(config, state, max_age=max(180, interval * 3))

    def start(self) -> int:
        self.state.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        lock_path = self.state.with_name(self.state.name + ".lock")

        with lock_path.open("a") as lock:
            try:
                portalocker.lock(lock, portalocker.LOCK_EX | portalocker.LOCK_NB)
            except portalocker.exceptions.LockException as error:
                raise ValueError("Сервис для этого файла состояния уже запущен") from error

            try:
                asyncio.run(self.run())
            except KeyboardInterrupt:
                pass

        return 0

    async def run(self) -> None:
        alerts.configured_channels()
        webhook = self.max_webhook()
        tasks = []
        previous = signal.signal(signal.SIGTERM, self.request_stop)

        try:
            if webhook:
                tasks.append(asyncio.create_task(asyncio.to_thread(webhook.run, self.stop)))

            if settings.get("TELEGRAM_POLL_UPDATES") == "1":
                tasks.append(
                    asyncio.create_task(poll(bot_token("telegram"), self.chat_commands("TELEGRAM")))
                )

            while not self.stop.is_set():
                for task in tasks:
                    if task.done():
                        task.result()
                        return

                try:
                    await asyncio.to_thread(monitor.run, self.config, self.state, False)
                except (ValueError, OSError, sqlite3.Error) as error:
                    print(f"Мониторинг: {error}", flush=True)

                await asyncio.to_thread(self.stop.wait, self.interval)
        finally:
            self.stop.set()
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            signal.signal(signal.SIGTERM, previous)

    def request_stop(self, _signum: int, _frame: object) -> None:
        self.stop.set()

    def max_webhook(self) -> MaxWebhook | None:
        if port := settings.get("MAX_WEBHOOK_PORT"):
            port_number = int(port)

            if not 1024 <= port_number <= 65535:
                raise ValueError("MAX_WEBHOOK_PORT должен быть в диапазоне 1024-65535")

            bot_token("max")
            responder = ChatResponder(
                "MAX",
                send_max_reply,
                self.chat_commands("MAX"),
                settings.get("MAX_BOT_USERNAME"),
            )
            return MaxWebhook(port_number, settings.get("MAX_WEBHOOK_SECRET", ""), responder)

        return None

    def chat_commands(self, platform: str) -> ChatCommands:
        ids = parse_admin_ids(settings.get(f"{platform}_ADMIN_IDS", ""))

        return ChatCommands(self.checks, ids)
