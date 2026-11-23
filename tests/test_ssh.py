import asyncio
from dataclasses import replace

import asyncssh
import pytest

from src.config import Node
from src.services import inspect_service


def test_ssh_status_logs_and_authentication(tmp_path):
    class Server(asyncssh.SSHServer):
        def begin_auth(self, username):
            return True

        def password_auth_supported(self):
            return True

        def validate_password(self, username, password):
            return username == "tester" and password == "fixture"

    async def exercise():
        key = asyncssh.generate_private_key("ssh-ed25519")
        replies = {
            "systemctl show x-ui -p LoadState -p ActiveState -p SubState": "ActiveState=active",
            "journalctl --no-pager -q -n 100 -u x-ui": "Служба запущена",
        }

        async def respond(process):
            process.stdout.write(replies[process.command] + "\n")
            process.exit(0)

        async with await asyncssh.create_server(
            Server, "127.0.0.1", 0, server_host_keys=[key], process_factory=respond
        ) as server:
            port = server.get_port()
            hosts = tmp_path / "known_hosts"
            hosts.write_bytes(f"[127.0.0.1]:{port} ".encode() + key.export_public_key())
            node = Node("local", "127.0.0.1", "tester", "fixture", ("x-ui",), port, hosts)
            assert await inspect_service(node, "x-ui", logs=False) == "ActiveState=active"
            assert await inspect_service(node, "x-ui", logs=True) == "Служба запущена"
            with pytest.raises(ValueError, match="отказано"):
                await inspect_service(replace(node, password="wrong"), "x-ui", logs=True)

    asyncio.run(exercise())
