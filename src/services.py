import asyncio

import asyncssh

from .config import Node


async def inspect_service(node: Node, service: str, logs: bool) -> str:
    if service not in node.services:
        raise ValueError("Служба отсутствует в инвентаре")

    command = (
        f"journalctl --no-pager -q -n 100 -u {service}"
        if logs
        else f"systemctl show {service} -p LoadState -p ActiveState -p SubState"
    )

    try:
        async with asyncio.timeout(20):
            async with asyncssh.connect(
                node.host,
                port=node.port,
                username=node.user,
                connect_timeout=5,
                password=node.password,
                known_hosts=str(node.known_hosts) if node.known_hosts else (),
                config=None,
                client_keys=[],
                agent_path=None,
            ) as connection:
                result = await connection.run(
                    command, check=False, encoding="utf-8", errors="replace"
                )
    except TimeoutError:
        raise ValueError("SSH: истекло время ожидания") from None
    except asyncssh.HostKeyNotVerifiable:
        raise ValueError("SSH: проверьте ключ сервера в known_hosts") from None
    except asyncssh.PermissionDenied:
        raise ValueError("SSH: отказано в доступе") from None
    except (asyncssh.Error, OSError):
        raise ValueError("SSH: соединение недоступно") from None

    if result.exit_status != 0:
        raise ValueError(f"Команда на сервере завершилась с кодом {result.exit_status}")

    output = str(result.stdout or "").strip()
    return "".join(c for c in output if c.isprintable() or c in "\n\t") or "Нет записей."
