import os
import re
import shutil
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import dotenv_values, set_key


@dataclass(frozen=True)
class Node:
    name: str
    host: str
    user: str
    password: str = field(repr=False)
    services: tuple[str, ...]
    port: int
    known_hosts: Path | None


def inventory(env_file: Path) -> list[Node]:
    values = {**dotenv_values(env_file, interpolate=False), **os.environ}
    if not values.get("VPN_NODES"):
        return []

    names = [name.strip() for name in (values.get("VPN_NODES") or "").split(",")]

    if any(not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", name) for name in names):
        raise ValueError("Укажите имена узлов через запятую в VPN_NODES")

    if len({name.upper() for name in names}) != len(names):
        raise ValueError("Имена узлов должны быть уникальными")

    nodes = []

    for name in names:
        prefix = name.upper()

        def required(key: str) -> str:
            variable = f"{prefix}_{key}"
            value = values.get(variable)

            if not value:
                raise ValueError(f"Заполните {variable}")

            return value

        services = tuple(unit.strip() for unit in required("SERVICES").split(","))

        if not all(re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.@-]*", unit) for unit in services):
            raise ValueError(f"Проверьте имена служб {prefix}_SERVICES")

        port = values.get(f"{prefix}_SSH_PORT") or "22"

        if not port.isascii() or not port.isdecimal() or not 1 <= int(port) <= 65535:
            raise ValueError(f"Проверьте порт {prefix}_SSH_PORT")

        known = values.get(f"{prefix}_SSH_KNOWN_HOSTS")
        hosts = env_file.parent / Path(known).expanduser() if known else None

        if hosts and not hosts.is_file():
            raise ValueError(f"Для узла {name} нужен файл проверенных ключей SSH")

        nodes.append(
            Node(
                name,
                required("SSH_HOST"),
                required("SSH_USER"),
                required("SSH_PASSWORD"),
                services,
                int(port),
                hosts,
            )
        )

    return nodes


@contextmanager
def inventory_lock(path: Path):
    lock = path.with_suffix(path.suffix + ".lock")

    try:
        handle = lock.open("x")
    except FileExistsError:
        raise ValueError(f"Файл занят. Проверьте блокировку {lock.name}") from None

    try:
        yield
    finally:
        handle.close()
        lock.unlink()


def add_node(path: Path, name: str, fields: dict[str, str]) -> Node:
    name = name.strip()
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", name):
        raise ValueError("Имя должно начинаться с буквы и содержать латиницу, цифры или _")

    if "VPN_NODES" in os.environ:
        raise ValueError("VPN_NODES задана процессом. Для сохранения задайте её в файле.")

    with inventory_lock(path):
        existing = inventory(path)
        keys = {f"{name.upper()}_{suffix}": value for suffix, value in fields.items()}
        occupied = set(dotenv_values(path, interpolate=False)) | os.environ.keys()

        if any(node.name.upper() == name.upper() for node in existing) or keys.keys() & occupied:
            raise ValueError("Узел или параметры с таким именем уже существуют")

        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
            draft = Path(handle.name)

        try:
            if path.exists():
                shutil.copyfile(path, draft)

            with draft.open("a", encoding="utf-8") as output:
                output.write("\n")

                for key, value in keys.items():
                    escaped = value.replace("\\", "\\\\").replace("'", "\\'")
                    output.write(f"{key}='{escaped}'\n")

            names = [node.name for node in existing] + [name]
            set_key(str(draft), "VPN_NODES", ",".join(names))
            added = inventory(draft)[-1]
            os.replace(draft, path)

            return added
        finally:
            draft.unlink(missing_ok=True)
