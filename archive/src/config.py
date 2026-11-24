import os
from pathlib import Path

from dotenv import dotenv_values


class Settings:
    def __init__(self) -> None:
        self.values: dict[str, str | None] = {}

    def load(self, path: Path, *, optional: bool = False) -> None:
        if optional and not path.is_file():
            self.values = {}

            return

        with path.open(encoding="utf-8-sig") as source:
            self.values = dotenv_values(stream=source, interpolate=False)

    def get(self, name: str, default: str = "") -> str:
        return os.environ.get(name, self.values.get(name) or default)


settings = Settings()
