import re

from vpn_manager.config import settings


def bot_token(platform: str) -> str:
    token = settings.get(f"{platform.upper()}_BOT_TOKEN")
    pattern = r"[0-9]+:[A-Za-z0-9_-]+" if platform == "telegram" else r"[\x21-\x7e]{1,2048}"

    if not re.fullmatch(pattern, token):
        raise ValueError(f"Задайте корректный {platform.upper()}_BOT_TOKEN")

    return token


def parse_admin_ids(value: str) -> frozenset[int]:
    tokens = [part.strip() for part in value.split(",") if part.strip()]

    if any(not re.fullmatch(r"[1-9][0-9]{0,18}", token) for token in tokens):
        raise ValueError("ID администраторов должны быть положительными числами через запятую")

    return frozenset(int(token) for token in tokens)
