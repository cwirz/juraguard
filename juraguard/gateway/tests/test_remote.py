import json
import socket
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from gateway.models import Integration
from gateway.remote import MAX_RESPONSE_BYTES, PinnedHTTPSConnection, RemoteError, call_tool, list_tools
from gateway.security import RemoteTarget, resolve_remote_target


TARGET = RemoteTarget("https", "remote.example", 443, "93.184.216.34", "/mcp")


class FakeResponse:
    def __init__(self, status=200, body=b"", headers=None, chunks=None):
        self.status = status
        self.headers = headers or {}
        self.chunks = list(chunks if chunks is not None else [body])
        self.read_count = 0

    def getheaders(self):
        return self.headers.items()

    def read1(self, _size):
        self.read_count += 1
        return self.chunks.pop(0) if self.chunks else b""


def json_response(request_id, *, result=None, error=None, headers=None):
    payload = {"jsonrpc": "2.0", "id": request_id}
    payload["error" if error is not None else "result"] = error if error is not None else result
    response_headers = {"Content-Type": "application/json", **(headers or {})}
    return FakeResponse(body=json.dumps(payload).encode(), headers=response_headers)


class FakeConnection:
    sock = None

    def __init__(self, handler):
        self.handler = handler
        self.response = None

    def request(self, method, path, body, headers):
        self.response = self.handler(method, path, json.loads(body), headers)

    def getresponse(self):
        return self.response

    def close(self):
        pass


class RemoteClientTests(TestCase):
    def setUp(self):
        user = get_user_model().objects.create_user("owner", password="password")
        self.integration = Integration.objects.create(
            workspace=user.workspace, name="Remote", slug="remote", remote_url="https://remote.example/mcp",
            write_enabled=True,
        )
        self.integration.set_headers({"Authorization": "Bearer upstream-only"})
        self.integration.save()

    def run_with_handler(self, handler):
        with (
            patch("gateway.remote.resolve_remote_target", return_value=TARGET),
            patch("gateway.remote._connection", side_effect=lambda *_: FakeConnection(handler)),
        ):
            return list_tools(self.integration)

    def test_initialize_notification_and_list_share_session(self):
        methods = []
        initialized = False

        def handler(method, path, payload, headers):
            nonlocal initialized
            self.assertEqual((method, path), ("POST", "/mcp"))
            self.assertEqual(headers["Authorization"], "Bearer upstream-only")
            self.assertEqual(headers["Accept-Encoding"], "identity")
            methods.append(payload["method"])
            if payload["method"] == "initialize":
                return json_response(1, result={"protocolVersion": "2025-06-18"}, headers={"Mcp-Session-Id": "s1"})
            self.assertEqual(headers["Mcp-Session-Id"], "s1")
            if payload["method"] == "notifications/initialized":
                initialized = True
                return FakeResponse(status=202)
            if not initialized:
                return FakeResponse(status=400)
            return json_response(2, result={"tools": [{"name": "hello"}]})

        self.assertEqual(self.run_with_handler(handler), [{"name": "hello"}])
        self.assertEqual(methods, ["initialize", "notifications/initialized", "tools/list"])

    def test_request_scoped_sse_stops_after_matching_response(self):
        matching = b'data: {"jsonrpc":"2.0","id":2,"result":{"tools":[{"name":"sse"}]}}\n\n'
        sse_response = FakeResponse(
            headers={"Content-Type": "text/event-stream"}, chunks=[matching, b"x" * (2 * 1024 * 1024)]
        )

        def handler(_method, _path, payload, _headers):
            if payload["method"] == "initialize":
                return json_response(1, result={})
            if payload["method"] == "notifications/initialized":
                return FakeResponse(status=202)
            return sse_response

        self.assertEqual(self.run_with_handler(handler)[0]["name"], "sse")
        self.assertEqual(sse_response.read_count, 1)

    def test_http_400_is_safe_error_without_direct_fallback(self):
        methods = []

        def handler(_method, _path, payload, _headers):
            methods.append(payload["method"])
            return FakeResponse(status=400)

        with self.assertRaisesMessage(RemoteError, "HTTP 400"):
            self.run_with_handler(handler)
        self.assertEqual(methods, ["initialize"])

    def test_read_only_hint_cannot_authorize_untrusted_remote_call(self):
        self.integration.write_enabled = False
        self.integration.tool_catalog = [{"name": "malicious", "annotations": {"readOnlyHint": True}}]

        with patch("gateway.remote._request") as request, self.assertRaisesMessage(
            RemoteError, "write access is not granted"
        ):
            call_tool(self.integration, "malicious", {})

        request.assert_not_called()

    def test_tool_annotations_must_be_an_object(self):
        for annotations in (None, "read-only", [], 1):
            with self.subTest(annotations=annotations), patch(
                "gateway.remote._request",
                return_value={"tools": [{"name": "malformed", "annotations": annotations}]},
            ), self.assertRaisesMessage(RemoteError, "catalog is invalid"):
                list_tools(self.integration)

    def test_rejects_invalid_json_rpc_and_encoded_responses(self):
        invalid_responses = [
            {"jsonrpc": "1.0", "id": 1, "result": {}},
            {"jsonrpc": "2.0", "id": 99, "result": {}},
            {"jsonrpc": "2.0", "id": 1, "result": {}, "error": {}},
            {"jsonrpc": "2.0", "id": 1, "error": {"code": "bad", "message": 1}},
            [],
        ]
        for payload in invalid_responses:
            with self.subTest(payload=payload):
                def invalid_handler(_method, _path, _request, _headers):
                    return FakeResponse(
                        body=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}
                    )

                with self.assertRaisesMessage(RemoteError, "invalid response"):
                    self.run_with_handler(invalid_handler)

        def encoded_handler(_method, _path, _payload, _headers):
            return FakeResponse(headers={"Content-Encoding": "gzip"})

        with self.assertRaisesMessage(RemoteError, "encoded response"):
            self.run_with_handler(encoded_handler)

    def test_response_size_and_monotonic_deadline_are_enforced(self):
        def oversized_handler(_method, _path, _payload, _headers):
            return FakeResponse(
                headers={"Content-Type": "application/json"}, chunks=[b"x" * MAX_RESPONSE_BYTES, b"x"]
            )

        with self.assertRaisesMessage(RemoteError, "size limit"):
            self.run_with_handler(oversized_handler)

        def slow_handler(_method, _path, _payload, _headers):
            return json_response(1, result={})

        with (
            patch("gateway.remote.time.monotonic", side_effect=[0, 0, 21]),
            self.assertRaisesMessage(RemoteError, "timed out"),
        ):
            self.run_with_handler(slow_handler)

    @patch("gateway.security.socket.getaddrinfo")
    def test_connection_uses_validated_ip_after_dns_changes(self, resolver):
        resolver.side_effect = [
            [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))],
            [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))],
        ]
        target = resolve_remote_target("https://remote.example/mcp")
        self.assertEqual(socket.getaddrinfo("remote.example", 443, type=socket.SOCK_STREAM)[0][4][0], "127.0.0.1")

        raw_socket = MagicMock()
        wrapped_socket = MagicMock()
        context = MagicMock()
        context.wrap_socket.return_value = wrapped_socket
        with (
            patch("gateway.remote.socket.create_connection", return_value=raw_socket) as connect,
            patch("gateway.remote.ssl.create_default_context", return_value=context),
        ):
            connection = PinnedHTTPSConnection(target, 5)
            connection.connect()

        connect.assert_called_once_with(("93.184.216.34", 443), 5)
        context.wrap_socket.assert_called_once_with(raw_socket, server_hostname="remote.example")
