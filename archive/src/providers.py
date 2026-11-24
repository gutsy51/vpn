import json
import os
import re
import subprocess
import webbrowser
from pathlib import Path
from urllib.parse import urlencode

from vpn_manager.config import settings

from .http import APIError, request

ADMINVPS_PORTAL = "https://my.adminvps.ru/"
FORNEX_API = "https://ru.fornex.com/api"
ONEDASH_API = "https://api.rdp-onedash.ru/api"
PRIVATE_FIELDS = re.compile(r"password|credential|secret|token|api.?key|private.?key", re.I)


def api(provider, path, method="GET", body=None):
    base, variable, scheme = {
        "fornex": (FORNEX_API, "FORNEX_API_KEY", "Api-Key"),
        "onedash": (ONEDASH_API, "ONEDASH_API_KEY", "Bearer"),
    }[provider]
    token = settings.get(variable, "")

    if not re.fullmatch(r"[\x21-\x7e]{1,1024}", token):
        raise ValueError(f"Задайте {variable}")

    response = request(
        base + path,
        method=method,
        body=body,
        headers={"Authorization": f"{scheme} {token}"},
    )

    if provider == "onedash" and (
        not isinstance(response, dict) or response.get("type") is not True
    ):
        raise APIError("OneDash не подтвердил запрос")

    return response


def public_result(value):
    if isinstance(value, dict):
        return {
            key: public_result(item)
            for key, item in value.items()
            if not PRIVATE_FIELDS.search(key)
        }

    if isinstance(value, list):
        return [public_result(item) for item in value]

    return value


def ticket(args):
    with args.body_file.open(encoding="utf-8-sig") as source:
        body = source.read(49_153).strip()

    subject = args.subject.strip()

    if not 1 <= len(subject) <= 64 or not 1 <= len(body) <= 49_152:
        raise ValueError("Тема должна содержать 1-64 символа, текст - 1-49152")

    if "\n" in subject or "\r" in subject:
        raise ValueError("Тема должна занимать одну строку")

    draft = {"subject": subject, "message": body}

    if args.order:
        draft["order"] = args.order

    return draft


def adminvps_ssh(alias: str, command: str) -> list[str]:
    target_options = []
    host = settings.get("ADMINVPS_SSH_HOST")
    known_hosts = settings.get("ADMINVPS_SSH_KNOWN_HOSTS")

    if host or known_hosts:
        if not host or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.-]{0,252}", host):
            raise ValueError("Укажите корректный IPv4 или DNS-адрес ADMINVPS_SSH_HOST")

        if not known_hosts:
            raise ValueError("Для отдельного SSH-адреса нужен ADMINVPS_SSH_KNOWN_HOSTS")

        pin = Path(known_hosts).expanduser().resolve()

        if not pin.is_file() or re.search(r'["\r\n]', str(pin)):
            raise ValueError("Проверьте файл ADMINVPS_SSH_KNOWN_HOSTS")

        target_options = [
            "-o",
            f"HostName={host}",
            "-o",
            f'UserKnownHostsFile="{pin}"',
            "-o",
            f"GlobalKnownHostsFile={os.devnull}",
            "-o",
            "UpdateHostKeys=no",
        ]

    return [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=yes",
        "-o",
        "ConnectTimeout=8",
        *target_options,
        alias,
        command,
    ]


def adminvps(args):
    if args.action == "portal":
        print(ADMINVPS_PORTAL)

        if args.open:
            webbrowser.open(ADMINVPS_PORTAL)
    elif args.action == "ssh-status":
        command = adminvps_ssh(
            args.alias,
            "systemctl is-active x-ui; service_status=$?; "
            'timedatectl show -p NTPSynchronized || exit $?; exit "$service_status"',
        )
        result = subprocess.run(command, check=False)

        return result.returncode
    else:
        draft = ticket(args)
        print(json.dumps({"portal": ADMINVPS_PORTAL, **draft}, ensure_ascii=False, indent=2))

    return 0


def fornex(args):
    action = args.action

    if action == "orders":
        return api("fornex", "/orders/vps/?" + urlencode({"page": args.page, "page_size": 25}))

    if action == "order":
        return api("fornex", f"/orders/vps/{args.order}/")

    if action == "power-status":
        return api("fornex", f"/vps/{args.order}/status/")

    if action == "power":
        command = {"start": "start", "reboot": "restart"}[args.command]

        if not args.apply:
            return {"preview": True, "order": args.order, "command": command}

        return api("fornex", f"/vps/{args.order}/{command}/", "POST", {})

    draft = ticket(args)

    if not args.send:
        return {"preview": True, **draft}

    return submit_fornex_ticket(draft)


def submit_fornex_ticket(draft):
    response = api("fornex", "/tickets/ticket/", "POST", draft)

    if not isinstance(response, dict):
        raise APIError("Fornex вернул неожиданный ответ на отправку тикета. Проверьте кабинет.")

    return {
        "submitted": True,
        **{key: response[key] for key in ("id", "number", "subject", "status") if key in response},
    }


def onedash(args):
    action = args.action

    if action in ("balance", "orders"):
        return api("onedash", f"/{action}")

    if action == "order":
        return api("onedash", "/orders/info", "POST", {"order_id": args.order})

    if action == "vps":
        return api("onedash", "/vps?" + urlencode({"page": args.page, "per_page": 25}))

    path = f"/vps/{args.vps_id}"

    if action == "ip-options":
        return api("onedash", path + "/ips")

    if action == "power-status":
        return api("onedash", path + "/power")

    if not args.apply:
        return {"preview": True, "vps_id": args.vps_id, "command": args.command}

    return api("onedash", path + "/power", "POST", {"action": args.command})
