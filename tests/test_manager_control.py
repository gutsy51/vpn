import asyncio
import subprocess
import sys

from src.manager_control import ManagerControl
from src.ui import Dashboard


def test_manager_lifecycle_from_dashboard(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    control = ManagerControl(env)
    children = []
    spawn = subprocess.Popen

    def local_service(args, **options):
        # Тестовый процесс использует тот же протокол остановки, только без Telegram и SSH.
        assert args[2:] == ["src.main", "--env-file", str(env), "serve"]
        script = (
            "import asyncio,sys; from pathlib import Path; "
            "from src.manager_control import ManagerControl; "
            "asyncio.run(ManagerControl(Path(sys.argv[1])).supervise(lambda: asyncio.sleep(60)))"
        )
        child = spawn([sys.executable, "-c", script, str(env)], **options)
        children.append(child)
        return child

    async def exercise():
        app = Dashboard([], env)

        async with app.run_test() as pilot:
            await pilot.press("6", "1")
            await app.workers.wait_for_complete()
            assert control.running()
            await pilot.press("1")
            await app.workers.wait_for_complete()
            assert len(children) == 1

        assert control.running()
        app = Dashboard([], env)

        async with app.run_test() as pilot:
            await pilot.press("6", "3")
            await app.workers.wait_for_complete()
            assert len(children) == 2
            assert children[0].wait(timeout=3) == 0
            assert control.running()
            await pilot.press("2")
            await app.workers.wait_for_complete()
            assert children[1].wait(timeout=3) == 0
            assert not control.running()

    monkeypatch.setattr(subprocess, "Popen", local_service)
    try:
        asyncio.run(exercise())
    finally:
        for child in children:
            if child.poll() is None:
                child.terminate()
                child.wait(timeout=5)
