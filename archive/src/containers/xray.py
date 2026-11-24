import base64
import binascii
import json
import re
from pathlib import Path
from uuid import UUID

from dotenv.parser import parse_stream

_CLIENTS = {
    "laptop": "XRAY_USER_LAPTOP_UUID",
    "android": "XRAY_USER_ANDROID_UUID",
    "friend-laptop": "XRAY_FRIEND_LAPTOP_UUID",
}
_HOSTNAME = re.compile(
    r"(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?",
    re.IGNORECASE,
)


def read_settings(path: Path) -> dict[str, str]:
    settings = {}

    with path.open(encoding="utf-8-sig") as source:
        for entry in parse_stream(source):
            name = entry.key

            if not entry.error and name is None:
                continue

            if entry.error or not name or name in settings or entry.value is None:
                line = entry.original.line
                raise ValueError(f"Некорректная или повторяющаяся переменная в строке {line}")

            settings[name] = entry.value

    return settings


def render(settings):
    target = settings["XRAY_REALITY_TARGET"]
    sni = settings["XRAY_REALITY_SNI"]
    host, _, port = target.rpartition(":")

    if (
        not _HOSTNAME.fullmatch(host)
        or not port.isascii()
        or not port.isdecimal()
        or not 1 <= int(port) <= 65535
    ):
        raise ValueError("XRAY_REALITY_TARGET должен иметь формат hostname:port")

    if not _HOSTNAME.fullmatch(sni):
        raise ValueError("XRAY_REALITY_SNI должен быть полным доменным именем")

    private_key = settings["XRAY_REALITY_PRIVATE_KEY"]

    if not re.fullmatch(r"[A-Za-z0-9_-]{43}", private_key):
        raise ValueError("XRAY_REALITY_PRIVATE_KEY должен быть ключом X25519 в формате Xray")

    try:
        if len(base64.urlsafe_b64decode(private_key + "=")) != 32:
            raise ValueError("Некорректная длина XRAY_REALITY_PRIVATE_KEY")
    except binascii.Error:
        raise ValueError("Некорректный XRAY_REALITY_PRIVATE_KEY") from None

    short_id = settings["XRAY_REALITY_SHORT_ID"]

    if not re.fullmatch(r"[a-fA-F0-9]{16}", short_id):
        raise ValueError("XRAY_REALITY_SHORT_ID должен содержать 16 шестнадцатеричных цифр")

    clients = []

    for label, variable in _CLIENTS.items():
        try:
            identifier = UUID(settings[variable])
        except ValueError:
            raise ValueError(f"{variable} должен содержать UUID") from None

        if identifier.version != 4:
            raise ValueError(f"{variable} должен содержать UUID версии 4")

        clients.append({"id": str(identifier), "email": label, "flow": "xtls-rprx-vision"})

    if len({client["id"] for client in clients}) != len(clients):
        raise ValueError("Для каждого устройства требуется отдельный UUID")

    return {
        "log": {"loglevel": "warning", "access": "none"},
        "inbounds": [
            {
                "port": 8443,
                "protocol": "vless",
                "settings": {"decryption": "none", "clients": clients},
                "streamSettings": {
                    "network": "raw",
                    "security": "reality",
                    "realitySettings": {
                        "target": target,
                        "serverNames": [sni],
                        "privateKey": private_key,
                        "shortIds": [short_id],
                    },
                },
            }
        ],
        "outbounds": [{"protocol": "freedom", "tag": "direct"}],
    }


def write_config(settings_path: Path, output_path: Path) -> None:
    try:
        config = render(read_settings(settings_path))
    except KeyError as error:
        raise ValueError(f"Отсутствует переменная {error.args[0]}") from None

    with output_path.open("x", encoding="utf-8") as output:
        json.dump(config, output, indent=2)
        output.write("\n")
