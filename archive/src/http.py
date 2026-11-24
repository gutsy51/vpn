import json
import socket
from http.client import HTTPException
from ssl import SSLCertVerificationError, SSLError, create_default_context
from urllib.error import HTTPError, URLError
from urllib.request import (
    HTTPRedirectHandler,
    HTTPSHandler,
    ProxyHandler,
    Request,
    build_opener,
)


class APIError(RuntimeError):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        return None


def transport_error(error):
    reason = error.reason if isinstance(error, URLError) else error

    if isinstance(reason, SSLCertVerificationError):
        return "Сертификат API не прошёл проверку TLS"

    if isinstance(reason, SSLError):
        return "Не удалось установить TLS-соединение"

    if isinstance(reason, TimeoutError):
        return "Истёк тайм-аут соединения с API"

    if isinstance(reason, socket.gaierror):
        return "Не удалось разрешить DNS-имя API"

    return "Не удалось соединиться с API"


def request(
    url, *, method="GET", body=None, headers=None, ca_file=None, timeout=10, expect_json=True
):
    if not url.startswith("https://"):
        raise APIError("Требуется HTTPS")

    try:
        context = create_default_context()

        if ca_file:
            context.load_verify_locations(cafile=ca_file)

        opener = build_opener(ProxyHandler({}), NoRedirect(), HTTPSHandler(context=context))
        request_headers = {"Accept": "application/json", **(headers or {})}
        payload = None

        if body is not None:
            payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
            request_headers["Content-Type"] = "application/json"

        with opener.open(
            Request(url, payload, request_headers, method=method), timeout=timeout
        ) as response:
            if not expect_json:
                return None

            raw = response.read(2_000_001)

            if len(raw) > 2_000_000:
                raise APIError("Слишком большой ответ API")

            return json.loads(raw) if raw else None
    except HTTPError as error:
        raise APIError(f"HTTP {error.code}") from None
    except (OSError, URLError, HTTPException) as error:
        raise APIError(transport_error(error)) from None
    except ValueError:
        raise APIError("Не удалось разобрать ответ или параметры API") from None
