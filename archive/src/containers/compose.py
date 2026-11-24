import os
import re
import shutil
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

import portalocker

from .xray import write_config


class ComposeError(RuntimeError):
    pass


class ComposeManager:
    actions = {
        "status": ("ps", "--all"),
        "logs": ("logs", "--tail", "100"),
        "up": ("up", "-d", "--no-deps"),
        "start": ("start",),
        "stop": ("stop",),
        "restart": ("restart", "--no-deps"),
        "kill": ("kill",),
    }

    def __init__(self, root: Path):
        self.root = root.resolve()
        self.workspace = self.root / ".local"
        self.current = self.workspace / "xray.json"
        self.previous = self.workspace / "xray.previous.json"

    def command(self, *args: str) -> list[str]:
        return ["docker", "compose", "--env-file", os.devnull, "-f", "compose.yaml", *args]

    def invoke(self, *args: str, config: Path | None = None) -> None:
        command = self.command(*args)
        env = {**os.environ, "XRAY_CONFIG_FILE": str(config or self.current)}
        result = subprocess.run(command, cwd=self.root, env=env, check=False)

        if result.returncode:
            raise ComposeError(f"Docker Compose завершился с кодом {result.returncode}")

    def validate(self) -> None:
        self.invoke("config", "--quiet")

    def manage(self, action: str, service: str) -> None:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", service):
            raise ValueError("Укажите имя сервиса из compose.yaml")

        self.invoke(*self.actions[action], service)

    def prepare(self, settings: Path | None, *, deploy: bool) -> None:
        self.workspace.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.workspace.chmod(0o700)

        with (self.workspace / "deploy.lock").open("a") as lock:
            portalocker.lock(lock, portalocker.LOCK_EX)

            with TemporaryDirectory(prefix="deploy.", dir=self.workspace) as directory:
                candidate = Path(directory) / "xray.json"

                if settings is not None:
                    write_config(self.root / settings, candidate)
                else:
                    shutil.copyfile(self.current, candidate)

                candidate.chmod(0o644)
                self.test(candidate)

                if deploy:
                    self.publish(candidate)

    def test(self, config: Path) -> None:
        self.invoke(
            "run",
            "--rm",
            "--no-deps",
            "--pull",
            "missing",
            "xray",
            "run",
            "-test",
            "-c",
            "/etc/personal-vpn/xray.json",
            config=config,
        )

    def publish(self, candidate: Path) -> None:
        has_previous = self.current.is_file()

        if has_previous:
            shutil.copy2(self.current, self.previous)

        candidate.replace(self.current)

        try:
            self.invoke("up", "-d", "--no-deps", "--force-recreate", "xray")
        except ComposeError:
            if has_previous:
                shutil.copy2(self.previous, candidate)
                candidate.replace(self.current)
                self.invoke("up", "-d", "--no-deps", "--force-recreate", "xray")

            raise

        self.manage("status", "xray")
