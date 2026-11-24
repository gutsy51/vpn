import os
import re
import shutil
import socket
import subprocess
import tomllib
from contextlib import closing
from dataclasses import dataclass
from struct import pack
from urllib.parse import urlsplit

LABEL = re.compile(r"[\w.-]{1,48}")


@dataclass(frozen=True)
class Check:
    name: str
    kind: str
    host: str = ""
    port: int = 0
    url: str = ""
    proxy_port: int = 0
    status: int = 204
    public: bool = False


def load_config(path):
    with path.open("rb") as file:
        config = tomllib.load(file)

    probe = config.get("probe", "")
    threshold = config.get("failure_threshold", 3)

    if not isinstance(probe, str) or not LABEL.fullmatch(probe):
        raise ValueError("Задайте короткое имя точки проверки в probe")

    if type(threshold) is not int or not 1 <= threshold <= 10:
        raise ValueError("failure_threshold должен быть от 1 до 10")

    entries = config.get("checks", [])

    if not isinstance(entries, list) or not 1 <= len(entries) <= 24:
        raise ValueError("Требуется от 1 до 24 проверок")

    checks = []

    for item in entries:
        check = Check(**item)

        validate_check(check)
        checks.append(check)

    if len({check.name for check in checks}) != len(checks):
        raise ValueError("Имена проверок должны быть уникальными")

    if any(check.kind != "tcp" for check in checks) and not shutil.which("curl"):
        raise ValueError("Для HTTPS-проверок нужен curl")

    return probe, threshold, checks


def validate_check(check: Check) -> None:
    if not LABEL.fullmatch(check.name):
        raise ValueError("Некорректное имя проверки")

    if type(check.public) is not bool or (
        type(check.proxy_port) is not int or not 0 <= check.proxy_port <= 65535
    ):
        raise ValueError(f"{check.name}: проверьте public и proxy_port")

    if check.kind == "tcp":
        if (
            not isinstance(check.host, str)
            or not check.host
            or type(check.port) is not int
            or not 1 <= check.port <= 65535
        ):
            raise ValueError(f"{check.name}: укажите host и port")
    elif check.kind in ("https", "tunnel"):
        if not isinstance(check.url, str):
            raise ValueError(f"{check.name}: URL должен быть строкой")

        url = urlsplit(check.url)

        if url.scheme != "https" or not url.hostname or url.username is not None:
            raise ValueError(f"{check.name}: требуется URL HTTPS без учётных данных")

        if type(check.status) is not int or not 200 <= check.status <= 299:
            raise ValueError(f"{check.name}: ожидается HTTP-статус 2xx")

        if check.kind == "tunnel" and (
            type(check.proxy_port) is not int or not 1 <= check.proxy_port <= 65535
        ):
            raise ValueError(f"{check.name}: укажите порт локального SOCKS")
    else:
        raise ValueError(f"{check.name}: допустимы tcp, https и tunnel")


def read_socket(connection: socket.socket, size: int) -> bytes:
    data = bytearray()

    while len(data) < size:
        chunk = connection.recv(size - len(data))

        if not chunk:
            raise OSError("SOCKS закрыл соединение")

        data.extend(chunk)

    return bytes(data)


def tcp_via_socks(check: Check) -> None:
    host = check.host.encode("idna")

    if not 1 <= len(host) <= 255:
        raise OSError("Неподдерживаемое имя узла для SOCKS")

    with socket.create_connection(("127.0.0.1", check.proxy_port), timeout=5) as connection:
        connection.sendall(b"\x05\x01\x00")

        if read_socket(connection, 2) != b"\x05\x00":
            raise OSError("SOCKS не принял соединение без аутентификации")

        request = b"\x05\x01\x00\x03" + bytes([len(host)]) + host + pack("!H", check.port)
        connection.sendall(request)

        if read_socket(connection, 4)[:2] != b"\x05\x00":
            raise OSError("SOCKS не подключился к узлу")


def probe_check(check: Check) -> tuple[bool, str]:
    if check.kind == "tcp":
        try:
            if check.proxy_port:
                tcp_via_socks(check)
            else:
                with closing(socket.create_connection((check.host, check.port), timeout=5)):
                    pass

            return True, "TCP соединение установлено"
        except (OSError, UnicodeError):
            return False, "TCP недоступен"

    try:
        output = subprocess.run(
            [
                "curl",
                "--disable",
                "--silent",
                "--output",
                os.devnull,
                "--write-out",
                "%{http_code}",
                "--max-time",
                "8",
                "--proto",
                "=https",
                "--noproxy",
                "",
                "--proxy",
                f"socks5h://127.0.0.1:{check.proxy_port}" if check.kind == "tunnel" else "",
                "--",
                check.url,
            ],
            capture_output=True,
            text=True,
            timeout=11,
        )
    except subprocess.TimeoutExpired:
        return False, "Истёк тайм-аут HTTPS"

    if output.returncode:
        return False, f"curl завершился с кодом {output.returncode}"

    return output.stdout == str(check.status), f"HTTP {output.stdout}"
