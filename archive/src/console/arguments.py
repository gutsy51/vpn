import argparse
import re
from pathlib import Path

from vpn_manager.containers.compose import ComposeManager
from vpn_manager.logs import SERVICES


def add_containers(groups) -> None:
    containers = groups.add_parser("container", help="Развёртывание и управление Docker Compose")
    containers.add_argument("--root", type=Path, default=Path.cwd())
    container_actions = containers.add_subparsers(dest="action", required=True)
    container_actions.add_parser("validate")
    container_actions.add_parser("test").add_argument("--settings", type=Path)
    container_actions.add_parser("deploy").add_argument(
        "--settings", type=Path, default=Path(".env.deploy")
    )

    for action in ComposeManager.actions:
        container_actions.add_parser(action).add_argument("service", nargs="?", default="xray")


def add_bots(groups) -> None:
    bots = groups.add_parser("bot", help="Настройка приёма команд")
    bot_actions = bots.add_subparsers(dest="action", required=True)
    bot_actions.add_parser("max-subscribe").add_argument("url")


def add_monitoring(groups) -> None:
    monitoring = groups.add_parser("monitor")
    monitoring.add_argument("--state", type=Path, default=Path(".local/ops.sqlite3"))
    actions = monitoring.add_subparsers(dest="action", required=True)
    run = actions.add_parser("run")
    run.add_argument("--config", type=Path, required=True)
    run.add_argument("--dry-run", action="store_true")
    actions.add_parser("status")
    ping = actions.add_parser("test-alert")
    ping.add_argument("channel", choices=("telegram", "max"))


def add_adminvps(groups) -> None:
    admin = groups.add_parser("adminvps").add_subparsers(dest="action", required=True)
    admin.add_parser("portal").add_argument("--open", action="store_true")
    ticket_arguments(admin.add_parser("ticket-draft"), str)
    admin.add_parser("ssh-status").add_argument(
        "alias", type=ssh_alias, nargs="?", default="adminvps"
    )


def add_fornex(groups) -> None:
    fornex = groups.add_parser("fornex").add_subparsers(dest="action", required=True)
    fornex.add_parser("orders").add_argument("--page", type=positive, default=1)

    for action in ("order", "power-status"):
        fornex.add_parser(action).add_argument("order", type=fornex_order)

    power_arguments(fornex.add_parser("power"), ("order",), ("start", "reboot"))
    ticket_arguments(fornex.add_parser("ticket"), fornex_order, send=True)


def add_onedash(groups) -> None:
    onedash = groups.add_parser("onedash").add_subparsers(dest="action", required=True)

    for action in ("balance", "orders"):
        onedash.add_parser(action)

    onedash.add_parser("vps").add_argument("--page", type=positive, default=1)
    onedash.add_parser("order").add_argument("order", type=positive)

    for action in ("ip-options", "power-status"):
        onedash.add_parser(action).add_argument("vps_id", type=positive)

    power_arguments(onedash.add_parser("power"), ("vps_id",), ("start", "reboot", "shutdown"))


def positive(value):
    if not re.fullmatch(r"[0-9]{1,19}", value) or not 1 <= int(value) <= 2**63 - 1:
        raise argparse.ArgumentTypeError("Требуется положительный числовой ID")

    return int(value)


def fornex_order(value):
    if not re.fullmatch(r"[0-9]+-[0-9]+", value):
        raise argparse.ArgumentTypeError("ID заказа должен иметь вид 34-1234")

    return value


def ssh_alias(value):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,62}", value):
        raise argparse.ArgumentTypeError("Ожидается SSH-алиас из ~/.ssh/config")

    return value


def ticket_arguments(parser, order_type, send=False):
    parser.add_argument("--subject", required=True)
    parser.add_argument("--body-file", required=True, type=Path)
    parser.add_argument("--order", type=order_type)

    if send:
        parser.add_argument("--send", action="store_true", help="Отправить тикет в поддержку")


def power_arguments(parser, form, commands):
    parser.add_argument(*form, type=fornex_order if form == ("order",) else positive)
    parser.add_argument("command", choices=commands)
    parser.add_argument("--apply", action="store_true", help="Отправить команду провайдеру")


def parser():
    root = argparse.ArgumentParser(prog="vpn-manager")
    root.add_argument(
        "--env-file", type=Path, help="Локальный файл API-токенов без shell-подстановок"
    )
    groups = root.add_subparsers(dest="group")
    ui = groups.add_parser("ui", help="Интерактивная консоль управления")
    ui.add_argument("--state", type=Path, default=Path(".local/ops.sqlite3"))
    ui.add_argument("--config", type=Path, default=Path("ops/monitor.local.toml"))

    service = groups.add_parser("serve", help="Запуск постоянного мониторинга и ботов")
    service.add_argument("--config", type=Path, required=True)
    service.add_argument("--state", type=Path, default=Path(".local/ops.sqlite3"))
    service.add_argument("--interval", type=positive, default=120)

    groups.add_parser("logs", help="Просмотр журнала сервиса").add_argument(
        "service", choices=SERVICES
    )
    add_containers(groups)
    add_bots(groups)
    add_monitoring(groups)
    add_adminvps(groups)
    add_fornex(groups)
    add_onedash(groups)

    return root
