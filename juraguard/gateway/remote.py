import http.client
import json
import socket
import ssl
import time

from django.core.exceptions import ValidationError

from .security import RemoteTarget, clean_secret_headers, resolve_remote_target


PROTOCOL_VERSION = "2025-06-18"
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
TOTAL_TIMEOUT_SECONDS = 20
CONNECT_TIMEOUT_SECONDS = 5


class RemoteError(Exception):
    pass


class PinnedHTTPConnection(http.client.HTTPConnection):
    def __init__(self, target: RemoteTarget, timeout: float):
        super().__init__(target.hostname, target.port, timeout=timeout)
        self.address = target.address

    def connect(self):
        self.sock = socket.create_connection((self.address, self.port), self.timeout)


class PinnedHTTPSConnection(PinnedHTTPConnection):
    def __init__(self, target: RemoteTarget, timeout: float):
        super().__init__(target, timeout)
        self.context = ssl.create_default_context()
        if ssl.HAS_ALPN:
            self.context.set_alpn_protocols(["http/1.1"])

    def connect(self):
        super().connect()
        self.sock = self.context.wrap_socket(self.sock, server_hostname=self.host)


def _connection(target: RemoteTarget, timeout: float):
    connection_class = PinnedHTTPSConnection if target.scheme == "https" else PinnedHTTPConnection
    return connection_class(target, timeout)


def _remaining(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise RemoteError("Remote MCP request timed out.")
    return remaining


def _validated_response(payload, request_id: int):
    if not isinstance(payload, dict):
        raise RemoteError("Remote MCP server returned an invalid response.")
    has_result = "result" in payload
    has_error = "error" in payload
    if (
        payload.get("jsonrpc") != "2.0"
        or payload.get("id") != request_id
        or has_result == has_error
        or (
            has_error
            and (
                not isinstance(payload["error"], dict)
                or not isinstance(payload["error"].get("code"), int)
                or not isinstance(payload["error"].get("message"), str)
            )
        )
    ):
        raise RemoteError("Remote MCP server returned an invalid response.")
    return payload


def _read_chunk(response, connection, deadline: float) -> bytes:
    remaining = _remaining(deadline)
    if connection.sock:
        connection.sock.settimeout(remaining)
    return response.read1(8192)


def _read_json(response, connection, request_id: int, deadline: float):
    content = bytearray()
    while chunk := _read_chunk(response, connection, deadline):
        if len(content) + len(chunk) > MAX_RESPONSE_BYTES:
            raise RemoteError("Remote MCP response exceeded the size limit.")
        content.extend(chunk)
    try:
        return _validated_response(json.loads(content), request_id)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RemoteError("Remote MCP server returned an invalid response.") from exc


def _read_sse(response, connection, request_id: int, deadline: float):
    buffer = bytearray()
    event_data: list[str] = []
    total = 0
    while chunk := _read_chunk(response, connection, deadline):
        total += len(chunk)
        if total > MAX_RESPONSE_BYTES:
            raise RemoteError("Remote MCP response exceeded the size limit.")
        buffer.extend(chunk)
        while b"\n" in buffer:
            raw_line, _, buffer = buffer.partition(b"\n")
            try:
                line = raw_line.rstrip(b"\r").decode("utf-8")
            except UnicodeDecodeError as exc:
                raise RemoteError("Remote MCP server returned an invalid response.") from exc
            if line.startswith("data:"):
                event_data.append(line[5:].lstrip())
            elif not line and event_data:
                try:
                    payload = json.loads("\n".join(event_data))
                except json.JSONDecodeError as exc:
                    raise RemoteError("Remote MCP server returned an invalid response.") from exc
                event_data = []
                if (
                    isinstance(payload, dict)
                    and "id" not in payload
                    and payload.get("jsonrpc") == "2.0"
                    and isinstance(payload.get("method"), str)
                ):
                    continue
                return _validated_response(payload, request_id)
    raise RemoteError("Remote MCP server returned no matching response.")


def _post(integration, payload: dict, deadline: float, session_id: str | None = None):
    try:
        target = resolve_remote_target(integration.remote_url)
        if integration.provider_type == integration.GENERIC_OAUTH and integration.encrypted_oauth_state:
            from .upstream_oauth import UpstreamOAuthError, authorization_headers
            try:
                headers = authorization_headers(integration)
            except UpstreamOAuthError as exc:
                raise RemoteError(str(exc)) from exc
        else:
            headers = clean_secret_headers(integration.get_headers())
    except (ValidationError, ValueError) as exc:
        raise RemoteError(str(exc)) from exc
    headers.update({
        "Accept": "application/json, text/event-stream",
        "Accept-Encoding": "identity",
        "Content-Type": "application/json",
        "MCP-Protocol-Version": PROTOCOL_VERSION,
    })
    if session_id:
        headers["Mcp-Session-Id"] = session_id
    connection = _connection(target, min(CONNECT_TIMEOUT_SECONDS, _remaining(deadline)))
    try:
        body = json.dumps(payload, separators=(",", ":")).encode()
        connection.request("POST", target.path, body=body, headers=headers)
        response = connection.getresponse()
        response_headers = {}
        for name, value in response.getheaders():
            lower_name = name.lower()
            response_headers[lower_name] = f"{response_headers[lower_name]}, {value}" if lower_name in response_headers else value
        if 300 <= response.status < 400:
            raise RemoteError("Remote MCP redirects are not allowed.")
        if response.status >= 400:
            raise RemoteError(f"Remote MCP server returned HTTP {response.status}.")
        content_encoding = response_headers.get("content-encoding", "identity").strip().lower()
        if content_encoding != "identity":
            raise RemoteError("Remote MCP server returned an encoded response.")
        try:
            declared_length = int(response_headers.get("content-length", "0"))
        except ValueError as exc:
            raise RemoteError("Remote MCP server returned an invalid response.") from exc
        if declared_length < 0 or declared_length > MAX_RESPONSE_BYTES:
            raise RemoteError("Remote MCP response exceeded the size limit.")
        returned_session = response_headers.get("mcp-session-id")
        if "id" not in payload:
            if response.status not in {200, 202, 204}:
                raise RemoteError("Remote MCP notification was rejected.")
            return {}, returned_session
        content_type = response_headers.get("content-type", "").partition(";")[0].strip().lower()
        if content_type == "text/event-stream":
            decoded = _read_sse(response, connection, payload["id"], deadline)
        elif content_type == "application/json":
            decoded = _read_json(response, connection, payload["id"], deadline)
        else:
            raise RemoteError("Remote MCP server returned an unsupported content type.")
        return decoded, returned_session
    except RemoteError:
        raise
    except (http.client.HTTPException, OSError, UnicodeError, ssl.SSLError, TimeoutError) as exc:
        raise RemoteError("Remote MCP server could not be reached.") from exc
    finally:
        connection.close()


def _request(integration, method: str, params: dict):
    deadline = time.monotonic() + TOTAL_TIMEOUT_SECONDS
    initialize = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "juraguard", "version": "0.1.0"},
        },
    }
    initialized, session_id = _post(integration, initialize, deadline)
    if "result" not in initialized:
        raise RemoteError("Remote MCP initialization failed.")
    notification = {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}
    _post(integration, notification, deadline, session_id)
    payload = {"jsonrpc": "2.0", "id": 2, "method": method, "params": params}
    response, _ = _post(integration, payload, deadline, session_id)
    if "result" not in response:
        raise RemoteError("Remote MCP server rejected the request.")
    return response["result"]


def list_tools(integration):
    result = _request(integration, "tools/list", {})
    tools = result.get("tools") if isinstance(result, dict) else None
    if not isinstance(tools, list):
        raise RemoteError("Remote MCP tool catalog is invalid.")
    valid = []
    for tool in tools:
        if isinstance(tool, dict) and not isinstance(tool.get("annotations", {}), dict):
            raise RemoteError("Remote MCP tool catalog is invalid.")
        if isinstance(tool, dict) and isinstance(tool.get("name"), str):
            valid.append(tool)
    return valid


def call_tool(integration, tool_name: str, arguments: dict):
    if not isinstance(arguments, dict):
        raise RemoteError("Tool arguments must be an object.")
    if not integration.write_enabled:
        raise RemoteError("Remote write access is not granted.")
    return _request(integration, "tools/call", {"name": tool_name, "arguments": arguments})
