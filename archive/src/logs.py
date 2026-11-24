import subprocess
from pathlib import Path

from .containers.compose import ComposeManager
from .providers import adminvps_ssh

SERVICES = ("xray", "x-ui")


def service_logs(service: str) -> str:
    if service == "x-ui":
        command = adminvps_ssh("adminvps", "journalctl --no-pager -q -u x-ui -n 100")
    elif service == "xray":
        command = ComposeManager(Path.cwd()).command("logs", "--no-color", "--tail", "100", "xray")
    else:
        raise ValueError("Доступные журналы: " + ", ".join(SERVICES))

    try:
        result = subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
        )
    except subprocess.TimeoutExpired:
        raise ValueError("Истёк тайм-аут чтения журнала") from None
    except OSError:
        raise ValueError(f"Не удалось запустить {command[0]}") from None

    if result.returncode:
        raise ValueError(f"Не удалось прочитать журнал {service} (код {result.returncode})")

    return result.stdout.strip() or "Журнал пуст."
