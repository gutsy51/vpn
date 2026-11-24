import asyncssh

from .config import Node


class ServerManager:
    def __init__(self, node: Node):
        self.node = node

    async def run(self, command: str) -> str:
        node = self.node
        try:
            async with asyncssh.connect(
                node.host,
                port=node.port,
                username=node.user,
                password=node.password,
                known_hosts=str(node.known_hosts) if node.known_hosts else (),
                config=None,
                client_keys=[],
                agent_path=None,
                connect_timeout=5,
            ) as ssh:
                result = await ssh.run(command, check=True, timeout=20)
        except (asyncssh.Error, OSError) as error:
            raise ValueError(f"{node.name}: ошибка управления ({type(error).__name__})") from None

        return str(result.stdout)

    async def change(self, action: str) -> None:
        if action not in {"start", "stop", "restart"}:
            raise ValueError("Неизвестная операция управления менеджером")

        await self.run(f"systemctl {action} vpn-manager.service")

    async def snapshot(self) -> tuple[str, str]:
        output = await self.run(
            "systemctl show vpn-manager.service -p ActiveState --value && "
            "if test -f /var/lib/vpn-manager/.local/logs/telegram.log; then "
            "tail -n 100 /var/lib/vpn-manager/.local/logs/telegram.log; fi"
        )
        state, _, log = output.partition("\n")
        label = {
            "active": "запущен",
            "inactive": "остановлен",
            "activating": "запускается",
            "deactivating": "останавливается",
            "failed": "авария",
        }.get(state.strip(), "состояние неизвестно")

        return label, log or "Журнала пока нет."
