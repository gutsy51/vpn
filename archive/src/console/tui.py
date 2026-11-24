import sqlite3
import sys
import unicodedata
from functools import partial
from pathlib import Path

from vpn_manager import providers
from vpn_manager.config import settings
from vpn_manager.containers.compose import ComposeError
from vpn_manager.http import APIError

from . import terminal


def printable(value):
    return "".join(
        char if not unicodedata.category(char).startswith("C") else " " for char in str(value)
    )


def cell(value, width):
    text = printable(value if value is not None else "-")

    return text if len(text) <= width else text[: width - 3] + "..."


def show(value):
    if isinstance(value, dict) and value.get("type") is True:
        value = value.get("data", {key: item for key, item in value.items() if key != "type"})

    print()
    lines = format_result(value)

    for line in lines:
        print("  " + line)


FIELDS = {
    "active": "Активен",
    "action": "Команда",
    "balance": "Баланс",
    "choices": "Доступные варианты",
    "completed": "Завершено",
    "currency": "Валюта",
    "days_remaining": "Осталось дней",
    "expiration_date": "Действует до",
    "first_ip_change_free_available": "Первая смена бесплатна",
    "id": "ID",
    "ip": "IP",
    "ipaddress_set": "IP-адреса",
    "location": "Локация",
    "name": "Название",
    "order": "Заказ",
    "order_id": "ID заказа",
    "os": "ОС",
    "power_action_status": "Статус команды",
    "power_state": "Питание",
    "price": "Цена",
    "requires_recreate": "Требуется пересоздание",
    "status": "Состояние",
    "submitted": "Отправлено",
    "tariff": "Тариф",
    "vps_id": "ID VPS",
}


def display_value(value):
    if isinstance(value, bool):
        return "Да" if value else "Нет"

    return printable(value) if value is not None else "-"


def format_result(value, indent=""):
    if isinstance(value, list):
        return [
            line
            for item in value
            for line in (
                format_result(item, indent + "  ")
                if isinstance(item, dict)
                else [f"{indent}- {display_value(item)}"]
            )
        ] or [f"{indent}-"]

    if isinstance(value, dict):
        lines = []

        for key, item in value.items():
            label = FIELDS.get(key, printable(key))

            if isinstance(item, (list, dict)):
                lines.append(f"{indent}{label}")
                lines.extend(format_result(item, indent + "  "))
            else:
                lines.append(f"{indent}{label:24} {display_value(item)}")

        return lines or [f"{indent}Ответ без данных."]

    return [indent + display_value(value)]


class Console:
    def __init__(self, parser, execute, state, config):
        self.parser = parser
        self.execute = execute
        self.state = state
        self.config = config
        self.breadcrumbs = ["VPN Manager"]

    def call(self, *command, display=True):
        args = self.parser.parse_args([str(part) for part in command])

        if args.group == "monitor" and args.action == "run":
            code, result = self.execute(args, monitor_report=self.show_probe)
        else:
            code, result = self.execute(args)

        if code:
            print(f"Команда завершилась с кодом {code}.")

        if display and result is not None:
            show(result)

        return result

    def attempt(self, action):
        try:
            action()
        except (APIError, ComposeError, OSError, TypeError, ValueError) as error:
            print(f"\nОшибка: {printable(error)}")
        except (KeyError, sqlite3.Error):
            print("\nПолучены неожиданные данные от провайдера или из файла состояния.")
        except SystemExit:
            return

    def menu(self, title, options, back="Назад"):
        self.breadcrumbs.append(title)
        selected = 0

        try:
            while True:
                selected = self.menu_selection(title, options, back, selected)

                if selected is None:
                    return

                self.attempt(options[selected][1])
        finally:
            self.breadcrumbs.pop()

    def menu_selection(self, title, options, back, selected):
        if terminal.interactive():
            print()
            choice = terminal.pick(
                "  >  ".join(self.breadcrumbs), [label for label, _ in options] + [back], selected
            )

            return None if choice in (None, len(options)) else choice

        while True:
            print(f"\n-- {title} --")

            for number, (label, _) in enumerate(options, 1):
                print(f"  {number}. {label}")

            print(f"  0. {back}")
            choice = input("Выбор > ").strip()

            if choice in ("0", "q", "Q"):
                return None

            choices = [str(number) for number in range(1, len(options) + 1)]

            if choice in choices:
                return choices.index(choice)

            print("Выберите номер из меню.")

    def run(self):
        print("\n" + terminal.accent("VPN MANAGER  /  Консоль управления"))

        for label, variable in (("Fornex", "FORNEX_API_KEY"), ("OneDash", "ONEDASH_API_KEY")):
            print(f"  {label}: {'ключ задан' if settings.get(variable) else 'ключ не задан'}")

        for label, prefix in (("Telegram", "TELEGRAM"), ("MAX", "MAX")):
            ready = settings.get(f"{prefix}_BOT_TOKEN") and settings.get(f"{prefix}_CHAT_ID")
            print(f"  {label}: {'настроен' if ready else 'требует настройки'}")

        try:
            self.menu(
                "Главное меню",
                [
                    ("Мониторинг и уведомления", self.monitoring),
                    ("AdminVPS", self.adminvps),
                    ("Fornex", self.fornex),
                    ("OneDash", self.onedash),
                    ("Docker-контейнеры", self.containers),
                ],
                back="Выход",
            )
        except (EOFError, KeyboardInterrupt):
            print()

        return 0

    def monitoring(self):
        self.menu(
            "Мониторинг",
            [
                ("Состояние и очередь уведомлений", self.monitor_status),
                ("Разовая проверка без уведомлений", self.monitor_probe),
                ("Тест доставки в Telegram", partial(self.test_alert, "telegram", "Telegram")),
                ("Тест доставки в MAX", partial(self.test_alert, "max", "MAX")),
            ],
        )

    def monitor_status(self):
        default = self.state
        system_state = Path("/var/lib/vpn-manager/state.sqlite3")

        if not default.is_file() and system_state.exists():
            default = system_state

        path = input(f"Файл состояния [{default}] > ").strip() or default
        status = self.call("monitor", "--state", Path(path).expanduser(), "status", display=False)

        if not isinstance(status, dict):
            raise ValueError("Не удалось получить статус монитора")

        print(f"\n  {'Точка':20} {'Проверка':24} {'Состояние':18} Последняя проба (UTC)")

        for row in status["checks"]:
            state = (
                "Сбой"
                if row["down"]
                else f"Неудач: {row['failures']}"
                if row["failures"]
                else "Доступно"
            )
            print(
                f"  {cell(row['probe'], 20):20} {cell(row['name'], 24):24} "
                f"{state:18} {cell(row['checked_at'], 25)}"
            )
            print(f"  {'':20} {cell(row['detail'], 76)}")

        pending = ", ".join(f"{name}: {count}" for name, count in status["pending"].items())
        print(f"  Очередь доставки: {pending or 'пуста'}")

    def monitor_probe(self):
        path = input(f"Конфигурация [{self.config}] > ").strip() or self.config
        self.call("monitor", "run", "--config", Path(path).expanduser(), "--dry-run")

    def show_probe(self, report):
        stamp = report["time"].removesuffix("+00:00").replace("T", " ")
        print(f"\n  {terminal.accent(printable(report['probe']))} | {stamp} UTC")

        for name, healthy, detail in report["results"]:
            status = terminal.accent("Доступно", "32") if healthy else terminal.accent("Сбой", "31")
            print(f"  {cell(name, 28):28} {status}  {printable(detail)}")

    def test_alert(self, channel, label):
        if self.confirm(f"Отправить тестовое сообщение в {label}?", label):
            self.call("monitor", "test-alert", channel)
            print("API принял сообщение.")

    def containers(self):
        self.menu(
            "Docker-контейнеры",
            [
                ("Проверить Compose", partial(self.call, "container", "validate")),
                ("Состояние Xray", partial(self.call, "container", "status", "xray")),
                ("Журнал Xray", partial(self.call, "container", "logs", "xray")),
                ("Проверить конфигурацию Xray", partial(self.call, "container", "test")),
                ("Развернуть Xray из .env.deploy", partial(self.container_change, "deploy")),
                ("Запустить Xray", partial(self.container_change, "start")),
                ("Остановить Xray", partial(self.container_change, "stop")),
                ("Перезапустить Xray", partial(self.container_change, "restart")),
                ("Принудительно остановить Xray", partial(self.container_change, "kill")),
            ],
        )

    def container_change(self, action):
        if self.confirm(f"Выполнить {action} для Xray на этом хосте?", "xray"):
            command = ("container", action) if action == "deploy" else ("container", action, "xray")
            self.call(*command)

    def adminvps(self):
        self.menu(
            "AdminVPS",
            [
                ("Кабинет: заказы и тикеты", partial(self.call, "adminvps", "portal", "--open")),
                ("Статус 3x-ui по SSH", self.admin_status),
                ("Журнал 3x-ui по SSH", partial(self.call, "logs", "x-ui")),
                ("Подготовить черновик тикета", partial(self.ticket, "adminvps")),
            ],
        )

    def admin_status(self):
        alias = input("SSH-алиас [adminvps] > ").strip() or "adminvps"
        self.call("adminvps", "ssh-status", alias)

    def fornex(self):
        self.menu(
            "Fornex",
            [
                ("Список VPS и управление", partial(self.browse, "fornex", "orders")),
                ("Открыть VPS по ID заказа", partial(self.open_by_id, "fornex", "orders")),
                ("Подготовить или отправить тикет", partial(self.ticket, "fornex")),
            ],
        )

    def onedash(self):
        self.menu(
            "OneDash",
            [
                ("Список VPS и управление", partial(self.browse, "onedash", "vps")),
                ("Открыть VPS по ID", partial(self.open_by_id, "onedash", "vps")),
                ("Заказы", partial(self.browse, "onedash", "orders")),
                ("Открыть заказ по ID", partial(self.open_by_id, "onedash", "orders")),
                ("Баланс", partial(self.call, "onedash", "balance")),
            ],
        )

    def browse(self, provider, collection):
        page = 1
        result = None
        query = ""
        selected = 0
        paginated = provider == "fornex" or collection == "vps"

        while True:
            if result is None:
                parameters = ("--page", page) if paginated else ()
                result = self.call(provider, collection, *parameters, display=False)

            if not isinstance(result, dict):
                raise ValueError("Провайдер не вернул данные")

            if provider == "fornex":
                rows, more = result["results"], bool(result.get("next"))
            elif collection == "vps":
                rows = result["data"]["items"]
                more = page < result["data"]["pagination"]["pages"]
            else:
                rows, more = result["data"], False

            title = f"{provider} > {'Заказы' if collection == 'orders' else 'VPS'} | стр. {page}"

            if query:
                title += f" | поиск: {printable(query)}"

            selectable = {}
            labels = []

            for row in rows:
                identifier = row.get("id", row.get("order_id"))
                tariff = row.get("tariff")
                name = row.get("name") or (
                    tariff.get("name") if isinstance(tariff, dict) else tariff
                )
                active = row.get("active")
                state = row.get("status")

                if state is None and isinstance(active, bool):
                    state = "Активен" if active else "Неактивен"

                if identifier is None or query not in f"{identifier} {name or ''}".casefold():
                    continue

                labels.append(f"{cell(identifier, 14):14} {cell(name, 25):25} {cell(state, 20)}")
                selectable[str(len(labels))] = identifier

            choice, selected = self.list_choice(title, labels, page, more, selected)

            if choice in ("0", "q"):
                return

            if choice == "n" and more:
                page += 1
                result = None
                selected = 0
            elif choice == "p" and page > 1:
                page -= 1
                result = None
                selected = 0
            elif choice in selectable:
                self.attempt(partial(self.resource, provider, collection, selectable[choice]))
            elif choice == "r":
                result = None
            elif choice == "/":
                query = (
                    input("ID или название на странице (Enter - сбросить) > ").strip().casefold()
                )
                selected = 0
            else:
                print("Выберите доступный номер или действие.")

    def list_choice(self, title, labels, page, more, selected):
        if terminal.interactive():
            actions = [(label, str(index)) for index, label in enumerate(labels, 1)]

            if page > 1:
                actions.append(("< Предыдущая страница", "p"))

            if more:
                actions.append(("Следующая страница >", "n"))

            actions.extend([("Поиск на странице", "/"), ("Обновить", "r"), ("Назад", "0")])
            shortcuts = {
                key.encode(): index for index, (_, key) in enumerate(actions) if not key.isdecimal()
            }
            print()

            if not labels:
                print("  На странице нет записей по этому запросу.")

            choice = terminal.pick(
                "VPN Manager > " + title, [label for label, _ in actions], selected, shortcuts
            )

            return ("0", 0) if choice is None else (actions[choice][1], choice)

        print(f"\n-- {title} --")
        print(f"  {'#':3} {'ID':14} {'Название':25} Состояние")

        for index, label in enumerate(labels, 1):
            print(f"  {index:<3} {label}")

        if not labels:
            print("  На странице нет записей по этому запросу.")

        paging = (" | p назад" if page > 1 else "") + (" | n дальше" if more else "")
        choice = input(f"Номер | / поиск | r обновить{paging} | 0 назад > ").strip().lower()

        return choice, 0

    def open_by_id(self, provider, collection):
        identifier = input("ID > ").strip()

        if identifier:
            self.resource(provider, collection, identifier)

    def resource(self, provider, collection, identifier):
        if provider == "onedash" and collection == "orders":
            self.call(provider, "order", identifier)

            return

        options = [("Состояние питания", partial(self.call, provider, "power-status", identifier))]

        if provider == "fornex":
            options.insert(0, ("Данные заказа", partial(self.call, provider, "order", identifier)))
            options.append(("Тикет по этому заказу", partial(self.ticket, provider, identifier)))
        else:
            options.append(
                ("Варианты смены IP и цена", partial(self.call, provider, "ip-options", identifier))
            )

        for label, action in (
            ("Запуск", "start"),
            ("Перезапуск", "reboot"),
            ("Выключение", "shutdown"),
        ):
            if action != "shutdown" or provider == "onedash":
                options.append((label, partial(self.power, provider, identifier, action, label)))

        self.menu(f"{provider} / {printable(identifier)}", options)

    def power(self, provider, identifier, action, label):
        self.call(provider, "power", identifier, action, display=False)

        if self.confirm(f"{label}: {provider}, VPS {printable(identifier)}?", str(identifier)):
            self.call(provider, "power", identifier, action, "--apply")

    def ticket(self, provider, identifier=None):
        subject = input("Тема (Enter - назад) > ").strip()

        if not subject:
            return

        path = input("Файл с текстом (Enter - назад) > ").strip()

        if not path:
            return

        order = (
            identifier if identifier is not None else input("ID заказа (необязательно) > ").strip()
        )
        command = "ticket-draft" if provider == "adminvps" else "ticket"
        parameters = [
            provider,
            command,
            f"--subject={subject}",
            "--body-file",
            Path(path).expanduser(),
        ]

        if order:
            parameters.extend(("--order", order))

        args = self.parser.parse_args([str(part) for part in parameters])
        draft = providers.ticket(args)
        print(f"\n  {terminal.accent(printable(draft['subject']))}")

        if draft.get("order"):
            print(f"  Заказ {printable(draft['order'])}")

        print()

        for line in draft["message"].splitlines():
            print(f"  {printable(line)}")

        if provider == "fornex" and self.confirm("Отправить этот тикет в Fornex?", "ОТПРАВИТЬ"):
            show(providers.submit_fornex_ticket(draft))
        elif provider == "adminvps":
            print(f"\n  Кабинет: {providers.ADMINVPS_PORTAL}")

    def confirm(self, question, phrase):
        print(question)

        if not sys.stdin.isatty():
            print("Для подтверждения откройте меню в интерактивном терминале.")

            return False

        if input(f"Введите {phrase} для подтверждения (Enter - отмена) > ").strip() == phrase:
            return True

        print("Отменено.")

        return False
