import logging
from collections import deque
from contextlib import contextmanager
from logging.handlers import RotatingFileHandler
from pathlib import Path

logger = logging.getLogger("vpn.telegram")


def log_path(env_file: Path) -> Path:
    return env_file.resolve().parent / ".local" / "logs" / "telegram.log"


class TelegramFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        if record.name.startswith("aiogram."):
            # Текст ошибки SDK может содержать URL запроса с токеном.
            event = "Ошибка обработки обновления"
            if record.name == "aiogram.dispatcher":
                event = "Сбой получения обновлений. Проверьте сеть и другие экземпляры бота"

            return f"{self.formatTime(record)} {record.levelname} {event}"

        return super().format(record)


@contextmanager
def record_events(env_file: Path):
    path = log_path(env_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(path, maxBytes=512_000, backupCount=2, encoding="utf-8")
    handler.setFormatter(TelegramFormatter("%(asctime)s %(levelname)s %(message)s"))
    sdk = logging.getLogger("aiogram")
    previous = logger.level
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)

    # Оба источника пишут через один обработчик, чтобы ротация работала одинаково.
    def relevant(record: logging.LogRecord) -> bool:
        return record.name == logger.name or record.levelno >= logging.WARNING

    handler.addFilter(relevant)
    sdk.addHandler(handler)

    try:
        yield
    finally:
        sdk.removeHandler(handler)
        logger.removeHandler(handler)
        logger.setLevel(previous)
        handler.close()


def tail(env_file: Path) -> str:
    try:
        with log_path(env_file).open(encoding="utf-8", errors="replace") as source:
            return "".join(deque(source, maxlen=100)).rstrip() or "Журнал пока пуст."
    except FileNotFoundError:
        return "Журнала пока нет. Запустите менеджер командой vpn-manager serve."
    except OSError:
        return "Не удалось прочитать журнал. Проверьте права доступа."
