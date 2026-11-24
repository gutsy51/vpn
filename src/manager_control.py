import asyncio
import os
import subprocess
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path
from uuid import uuid4

from filelock import FileLock, Timeout

from .bot_log import tail


class ManagerControl:
    def __init__(self, env_file: Path):
        self.env_file = env_file.resolve()
        self.directory = self.env_file.parent / ".local" / "run" / self.env_file.name
        self.directory.mkdir(parents=True, exist_ok=True)
        self.instance = self.directory / "instance"
        self.stop_request = self.directory / "stop"
        self.lock_path = self.directory / "serve.lock"

    def running(self) -> bool:
        try:
            with FileLock(self.lock_path, timeout=0):
                return False
        except Timeout:
            return True

    async def snapshot(self) -> tuple[str, str]:
        state = "запущен" if self.running() else "остановлен"

        return state, tail(self.env_file)

    async def supervise(self, service: Callable[[], Awaitable[None]]) -> None:
        lock = FileLock(self.lock_path, timeout=0)

        try:
            lock.acquire()
        except Timeout:
            raise ValueError("Менеджер уже запущен") from None

        try:
            identity = uuid4().hex
            self.stop_request.unlink(missing_ok=True)
            self.instance.write_text(identity, encoding="ascii")
            task = asyncio.ensure_future(service())

            try:
                while not task.done():
                    done, _ = await asyncio.wait({task}, timeout=0.2)

                    if done:
                        break

                    if self.stop_request.exists() and self.stop_request.read_text() == identity:
                        return

                await task
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        finally:
            try:
                self.instance.unlink(missing_ok=True)
                self.stop_request.unlink(missing_ok=True)
            finally:
                lock.release()

    async def stop(self) -> None:
        for _ in range(75):
            if not self.running():
                return

            try:
                identity = self.instance.read_text(encoding="ascii")
            except FileNotFoundError:
                identity = ""

            if identity and not self.stop_request.exists():
                self.stop_request.write_text(identity, encoding="ascii")

            await asyncio.sleep(0.2)

        raise ValueError("Менеджер не завершился за 15 секунд")

    async def start(self) -> None:
        if self.running():
            return

        process = subprocess.Popen(
            [sys.executable, "-m", "src.main", "--env-file", str(self.env_file), "serve"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=os.name != "nt",
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )

        for _ in range(75):
            await asyncio.sleep(0.2)

            if process.poll() is not None:
                raise ValueError(
                    "Менеджер завершился при запуске. Проверьте конфигурацию и журнал."
                )

            if self.running() and self.instance.exists():
                return

        raise ValueError("Запуск ещё не подтверждён. Проверьте состояние менеджера.")

    async def change(self, action: str) -> None:
        try:
            with FileLock(self.directory / "control.lock", timeout=0):
                if action in ("stop", "restart"):
                    await self.stop()

                if action in ("start", "restart"):
                    await self.start()
        except Timeout:
            raise ValueError("Уже выполняется операция управления") from None
