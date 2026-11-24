from dataclasses import dataclass
from enum import Enum
from typing import Literal


class ChatRole(Enum):
    USER = "user"
    ADMIN = "admin"


@dataclass(frozen=True)
class ChatAddress:
    kind: Literal["chat_id", "user_id"]
    identifier: int


@dataclass(frozen=True)
class IncomingMessage:
    sender_id: int
    destination: ChatAddress
    text: str
    timestamp: int = 0
    private_chat: bool = False


@dataclass(frozen=True)
class ProbeSummary:
    name: str
    state: str
    checked_at: str = ""


@dataclass(frozen=True)
class MonitorSummary:
    checks: tuple[ProbeSummary, ...]
    pending: tuple[tuple[str, int], ...] = ()
