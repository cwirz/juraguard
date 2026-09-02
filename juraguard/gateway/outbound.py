import http.client
import json
import ssl
import time
from dataclasses import dataclass
from urllib.parse import urlencode

from django.core.exceptions import ValidationError

from .remote import _connection
from .security import resolve_remote_target


MAX_RESPONSE_BYTES = 256 * 1024
TIMEOUT_SECONDS = 10


class OutboundError(Exception):
    pass


@dataclass(frozen=True)
class OutboundResponse:
    status: int
    headers: dict[str, str]
    body: bytes

    def json(self, expected_types=(dict,)):
        try:
            value = json.loads(self.body)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise OutboundError("Remote server returned invalid JSON.") from exc
        if not isinstance(value, expected_types):
            raise OutboundError("Remote server returned invalid JSON.")
        return value


def _set_remaining_timeout(connection, deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise OutboundError("Remote request timed out.")
    connection.sock.settimeout(remaining)


def request(url, *, method="GET", headers=None, form=None, json_body=None, allowed_status=(200,)):
    try:
        target = resolve_remote_target(url)
    except ValidationError as exc:
        raise OutboundError(str(exc)) from exc
    request_headers = {"Accept": "application/json", "Accept-Encoding": "identity", **(headers or {})}
    body = None
    if form is not None:
        body = urlencode(form).encode()
        request_headers["Content-Type"] = "application/x-www-form-urlencoded"
    elif json_body is not None:
        body = json.dumps(json_body, separators=(",", ":")).encode()
        request_headers["Content-Type"] = "application/json"
    deadline = time.monotonic() + TIMEOUT_SECONDS
    connection = _connection(target, TIMEOUT_SECONDS)
    try:
        connection.request(method, target.path, body=body, headers=request_headers)
        _set_remaining_timeout(connection, deadline)
        response = connection.getresponse()
        response_headers = {}
        for name, value in response.getheaders():
            key = name.lower()
            response_headers[key] = f"{response_headers[key]}, {value}" if key in response_headers else value
        if 300 <= response.status < 400:
            raise OutboundError("Remote redirects are not allowed.")
        if response.status not in allowed_status:
            raise OutboundError(f"Remote server returned HTTP {response.status}.")
        if response_headers.get("content-encoding", "identity").lower() != "identity":
            raise OutboundError("Remote server returned an encoded response.")
        content = bytearray()
        while True:
            _set_remaining_timeout(connection, deadline)
            chunk = response.read1(8192)
            if time.monotonic() >= deadline:
                raise OutboundError("Remote request timed out.")
            if not chunk:
                break
            if len(content) + len(chunk) > MAX_RESPONSE_BYTES:
                raise OutboundError("Remote response exceeded the size limit.")
            content.extend(chunk)
        return OutboundResponse(response.status, response_headers, bytes(content))
    except OutboundError:
        raise
    except (http.client.HTTPException, OSError, UnicodeError, ssl.SSLError, TimeoutError) as exc:
        raise OutboundError("Remote server could not be reached.") from exc
    finally:
        connection.close()
