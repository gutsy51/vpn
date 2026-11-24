from typing import Protocol

from vpn_manager.logs import SERVICES, service_logs
from vpn_manager.messaging.models import ChatRole, IncomingMessage, MonitorSummary


class CheckAccess(Protocol):
    def names(self, *, include_private: bool = False) -> tuple[str, ...]: ...

    def ping(self, name: str, *, include_private: bool = False) -> bool: ...

    def status(self, *, detailed: bool) -> MonitorSummary: ...


class ChatCommands:
    def __init__(self, checks: CheckAccess, admin_ids: frozenset[int]):
        self.checks = checks
        self.admin_ids = admin_ids

    def reply(self, message: IncomingMessage, bot_name: str) -> str | None:
        parts = message.text.split(maxsplit=1)

        if not parts or not parts[0].startswith("/"):
            return None

        command, _, recipient = parts[0].lower().partition("@")

        if recipient and recipient != bot_name.lower().lstrip("@"):
            return None

        role = ChatRole.ADMIN if message.sender_id in self.admin_ids else ChatRole.USER

        if command in ("/start", "/help"):
            return self.help_for(role)

        if command == "/whoami":
            label = "администратор" if role is ChatRole.ADMIN else "пользователь"

            return f"Платформенный ID: {message.sender_id}\nРоль: {label}"

        if command == "/status":
            return self.status_text(detailed=False)

        if command in ("/checks", "/check", "/ping"):
            private = role is ChatRole.ADMIN
            names = self.checks.names(include_private=private)

            if command == "/checks":
                return "\n".join(f"/check {name}" for name in names) or "Доступных проверок нет."

            name = parts[1].strip() if len(parts) == 2 else ""

            if name not in names:
                return "Укажите имя проверки: " + ", ".join(names)

            status = "доступен" if self.checks.ping(name, include_private=private) else "недоступен"

            return f"{name} - {status}"

        if command == "/logs":
            if role is not ChatRole.ADMIN:
                return "Эта команда доступна администраторам."

            if not message.private_chat:
                return "Откройте личный чат с ботом для просмотра журнала."

            if len(parts) != 2 or parts[1] not in SERVICES:
                return "Укажите сервис: " + ", ".join(SERVICES)

            try:
                return service_logs(parts[1])[-1900:]
            except ValueError as error:
                return str(error)

        if command == "/diagnostics":
            if role is not ChatRole.ADMIN:
                return "Эта команда доступна администраторам."

            return self.status_text(detailed=True)

        return None

    def status_text(self, *, detailed: bool) -> str:
        snapshot = self.checks.status(detailed=detailed)

        if not snapshot.checks:
            return "Результатов мониторинга пока нет."

        lines = []

        for check in snapshot.checks:
            line = f"{check.name} - {check.state}"

            if detailed and check.checked_at:
                line += f", последняя проверка {check.checked_at}"

            lines.append(line)

        if detailed:
            pending = ", ".join(f"{name}: {count}" for name, count in snapshot.pending)
            lines.append(f"Очередь уведомлений: {pending or 'пуста'}")

        return "\n".join(lines)[:3800]

    @staticmethod
    def help_for(role: ChatRole) -> str:
        commands = [
            "/status - состояние",
            "/checks - список серверов и панелей",
            "/check <имя> - проверить доступность",
            "/ping <имя> - та же проверка",
            "/whoami - роль и ID",
        ]

        if role is ChatRole.ADMIN:
            commands.append("/logs <сервис> - журнал в личном чате")
            commands.append("/diagnostics - время проб и очередь уведомлений")

        return "\n".join(commands)
