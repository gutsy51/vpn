import argparse
import asyncio
import sys
from io import TextIOWrapper
from pathlib import Path

from .config import inventory
from .server_manager import ServerManager
from .services import inspect_service
from .ui import Dashboard


def configure_output() -> None:
    if sys.platform == "win32":
        for stream in (sys.stdout, sys.stderr):
            if isinstance(stream, TextIOWrapper):
                stream.reconfigure(encoding="utf-8")


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="vpn-manager")
    parser.add_argument("--env-file", type=Path, default=Path(".env"))

    groups = parser.add_subparsers(dest="action")
    groups.add_parser("config").add_argument("operation", choices=["check"])
    groups.add_parser("ui")
    groups.add_parser("nodes")
    groups.add_parser("serve", help="Запустить менеджер с Telegram-ботом")
    groups.add_parser("start", help="Запустить службу менеджера на AdminVPS")
    groups.add_parser("attach", help="Открыть экран менеджера AdminVPS")

    for action in ("check", "logs"):
        command = groups.add_parser(action)
        command.add_argument("node")
        command.add_argument("service")

    return parser.parse_args()


def execute(args: argparse.Namespace) -> None:
    nodes = inventory(args.env_file)

    if args.action == "config":
        print(f"Конфигурация проверена. Узлов: {len(nodes)}")
    elif args.action == "serve":
        from .manager_control import ManagerControl
        from .telegram import serve

        asyncio.run(ManagerControl(args.env_file).supervise(lambda: serve(nodes, args.env_file)))
    elif args.action in ("start", "attach"):
        node = next((node for node in nodes if node.name.casefold() == "adminvps"), None)
        if node is None:
            raise ValueError("AdminVPS отсутствует в инвентаре")

        manager = ServerManager(node)
        if args.action == "start":
            asyncio.run(manager.change("start"))
            print("Служба менеджера на AdminVPS запущена")
        else:
            Dashboard(nodes, args.env_file, manager=manager).run()
    elif args.action == "nodes":
        for node in nodes:
            print(node.name, ", ".join(node.services))
    elif args.action in ("check", "logs"):
        node = next((node for node in nodes if node.name == args.node), None)

        if node is None:
            raise ValueError("Неизвестный узел")

        print(asyncio.run(inspect_service(node, args.service, logs=args.action == "logs")))
    else:
        Dashboard(nodes, args.env_file).run()


def main() -> int:
    configure_output()
    args = arguments()

    try:
        execute(args)
    except (ValueError, OSError) as error:
        print(error, file=sys.stderr)

        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
