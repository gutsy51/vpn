import argparse
import json
import os
import sys
from collections.abc import Callable
from io import TextIOWrapper
from pathlib import Path

from vpn_manager.config import settings

from . import providers
from .console.arguments import parser
from .console.tui import Console
from .containers.compose import ComposeError, ComposeManager
from .http import APIError
from .logs import service_logs
from .messaging import alerts
from .messaging.max import MaxWebhook
from .monitoring import monitor
from .service import ManagerService


def execute(args: argparse.Namespace, *, monitor_report: Callable | None = None):
    if args.group == "logs":
        print(service_logs(args.service))

        return 0, None

    if args.group == "serve":
        service = ManagerService(args.config, args.state.resolve(), args.interval)

        return service.start(), None

    if args.group == "container":
        manager = ComposeManager(args.root)

        if args.action == "validate":
            manager.validate()
        elif args.action in ("test", "deploy"):
            manager.prepare(args.settings, deploy=args.action == "deploy")
        else:
            manager.manage(args.action, args.service)

        return 0, None

    if args.group == "bot":
        MaxWebhook.subscribe(args.url, settings.get("MAX_WEBHOOK_SECRET"))
        print("MAX подтвердил регистрацию webhook")

        return 0, None

    if args.group == "monitor":
        if args.action == "run":
            return monitor.run(
                args.config, args.state.resolve(), args.dry_run, report=monitor_report
            ), None

        if args.action == "test-alert":
            if args.channel not in alerts.configured_channels():
                raise ValueError("Для канала не заданы токен и ID чата")

            alerts.send(args.channel, "VPN - тест доставки уведомлений")

            return 0, None

        return 0, monitor.status(args.state.resolve())

    if args.group == "adminvps":
        return providers.adminvps(args), None

    handler = {"fornex": providers.fornex, "onedash": providers.onedash}[args.group]

    return 0, providers.public_result(handler(args))


def main():
    if sys.platform == "win32":
        for stream in (sys.stdout, sys.stderr):
            if isinstance(stream, TextIOWrapper):
                stream.reconfigure(encoding="utf-8")

    command_parser = parser()
    args = command_parser.parse_args()

    if args.group is None:
        if not sys.stdin.isatty():
            command_parser.print_help()

            return 0

        args = command_parser.parse_args(
            (["--env-file", str(args.env_file)] if args.env_file else []) + ["ui"]
        )

    os.umask(0o077)

    try:
        settings.load(args.env_file or Path(".env"), optional=args.env_file is None)

        if args.group == "ui":
            return Console(command_parser, execute, args.state, args.config).run()

        code, result = execute(args)

        if result is not None:
            print(json.dumps(result, indent=2, ensure_ascii=False))
        elif args.group == "monitor" and args.action == "test-alert":
            print("API принял сообщение")

        return code
    except (APIError, ComposeError, OSError, TypeError, ValueError) as error:
        if isinstance(error, OSError):
            error = error.strerror or error.__class__.__name__

        print(error, file=sys.stderr)

        return 1


if __name__ == "__main__":
    sys.exit(main())
