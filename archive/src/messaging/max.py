import asyncio
import json
import re
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import asynccontextmanager
from hmac import compare_digest
from http.server import BaseHTTPRequestHandler, HTTPServer
from queue import Empty, Full, Queue
from ssl import create_default_context
from threading import Event, Thread
from time import time
from urllib.parse import urlsplit

from aiohttp import ClientError, TCPConnector
from maxapi import Bot
from maxapi.client.default import DefaultConnectionProperties
from maxapi.enums.update import UpdateType
from maxapi.exceptions.base import MaxError

from vpn_manager.config import settings
from vpn_manager.http import APIError
from vpn_manager.messaging.commands import ChatCommands
from vpn_manager.messaging.models import ChatAddress, IncomingMessage

from .settings import bot_token


@asynccontextmanager
async def max_bot() -> AsyncIterator[Bot]:
    connector = None

    if ca_file := settings.get("MAX_CA_FILE"):
        connector = TCPConnector(ssl=create_default_context(cafile=ca_file))

    try:
        connection = DefaultConnectionProperties(
            timeout=10, sock_connect=5, max_retries=0, connector=connector
        )
        bot = Bot(bot_token("max"), default_connection=connection, auto_requests=False)

        try:
            yield bot
        finally:
            await bot.close_session()

    except (ClientError, MaxError, OSError):
        raise APIError("Не удалось выполнить запрос к MAX") from None
    finally:
        if connector is not None:
            await connector.close()


async def send_max(address: ChatAddress, text: str) -> None:
    async with max_bot() as bot:
        message = await bot.send_message(
            chat_id=address.identifier if address.kind == "chat_id" else None,
            user_id=address.identifier if address.kind == "user_id" else None,
            text=text,
            disable_link_preview=True,
        )

        if message is None:
            raise APIError("MAX не подтвердил доставку")


def send_max_reply(address: ChatAddress, text: str) -> None:
    asyncio.run(send_max(address, text))


async def subscribe_max(url: str, secret: str) -> None:
    async with max_bot() as bot:
        result = await bot.subscribe_webhook(url, [UpdateType.MESSAGE_CREATED], secret)

        if not result.success:
            raise APIError("MAX не подтвердил регистрацию webhook")


def fields(value: object) -> Mapping[str, object]:
    return value if isinstance(value, dict) else {}


def parse_max(update: object) -> IncomingMessage | None:
    payload = fields(update)

    if payload.get("update_type") != "message_created":
        return None

    message = fields(payload.get("message"))
    sender = fields(message.get("sender"))
    sender_id = sender.get("user_id")
    recipient = fields(message.get("recipient"))
    text = fields(message.get("body")).get("text")

    if sender.get("is_bot") or type(sender_id) is not int or not isinstance(text, str):
        return None

    chat_id = recipient.get("chat_id")

    if recipient.get("chat_type") == "dialog":
        address = ChatAddress("user_id", sender_id)
    elif type(chat_id) is int:
        address = ChatAddress("chat_id", chat_id)
    else:
        return None

    timestamp = payload.get("timestamp")

    return IncomingMessage(
        sender_id,
        address,
        text,
        timestamp // 1000 if isinstance(timestamp, int) else 0,
        private_chat=address.kind == "user_id",
    )


class ChatResponder:
    def __init__(
        self,
        platform: str,
        send: Callable[[ChatAddress, str], None],
        commands: ChatCommands,
        username: str,
    ):
        self.platform = platform
        self.send = send
        self.commands = commands
        self.username = username

    def handle(self, message: IncomingMessage | None) -> None:
        if message is None or (message.timestamp and time() - message.timestamp > 300):
            return

        try:
            text = self.commands.reply(message, self.username)

            if text:
                self.send(message.destination, text)

        except (APIError, OSError, RuntimeError) as error:
            print(f"{self.platform}: не удалось обработать команду: {error}", flush=True)


class MaxWebhook:
    path = "/max/webhook"
    header = "X-Max-Bot-Api-Secret"

    def __init__(self, port: int, secret: str, responder: ChatResponder):
        self.secret = self.validate_secret(secret)
        self.responder = responder
        self.messages: Queue[IncomingMessage] = Queue(maxsize=100)
        self.server = HTTPServer(("127.0.0.1", port), self.handler())

    @staticmethod
    def validate_secret(secret: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9_-]{16,256}", secret):
            raise ValueError("MAX_WEBHOOK_SECRET должен содержать 16-256 латинских символов")

        return secret

    def handler(self) -> type[BaseHTTPRequestHandler]:
        webhook = self

        class Handler(BaseHTTPRequestHandler):
            def setup(self) -> None:
                self.request.settimeout(5)
                super().setup()

            def do_POST(self) -> None:
                if self.path != webhook.path:
                    self.respond(404)

                    return

                secret = self.headers.get(webhook.header, "").encode("utf-8")

                if not compare_digest(secret, webhook.secret.encode("ascii")):
                    self.respond(403)

                    return

                size = self.headers.get("Content-Length", "")

                if not re.fullmatch(r"[0-9]{1,6}", size) or not 1 <= int(size) <= 65536:
                    self.respond(413)

                    return

                try:
                    message = parse_max(json.loads(self.rfile.read(int(size))))

                    if message is not None:
                        webhook.messages.put_nowait(message)

                except (ValueError, RecursionError):
                    self.respond(400)

                    return
                except Full:
                    self.respond(503)

                    return

                self.respond(200)

            def respond(self, status: int) -> None:
                self.send_response(status)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_request(self, code="-", size="-") -> None:
                return None

        return Handler

    def run(self, stop: Event) -> None:
        listener = Thread(target=self.server.serve_forever, daemon=True)
        listener.start()

        try:
            while not stop.is_set():
                try:
                    message = self.messages.get(timeout=1)
                except Empty:
                    continue

                self.responder.handle(message)
                stop.wait(0.55)

        finally:
            self.server.shutdown()
            self.server.server_close()

    @classmethod
    def subscribe(cls, url: str, secret: str) -> None:
        parsed = urlsplit(url)

        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.port not in (None, 443)
            or parsed.username is not None
            or parsed.path != cls.path
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("Укажите HTTPS-адрес MAX webhook на порту 443 с путём /max/webhook")

        asyncio.run(subscribe_max(url, cls.validate_secret(secret)))
