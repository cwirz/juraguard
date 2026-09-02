import json
from urllib.parse import urlsplit

from django.conf import settings
from django.core.exceptions import ValidationError
from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .dispatch import execute
from .hardening import security_event
from .models import GatewayToken
from .oauth_server import authenticate_access_token, canonical_base, canonical_resource
from .security import opaque_hash
from .tool_defs import META_TOOLS


PROTOCOL_VERSION = "2025-06-18"
SUPPORTED_VERSIONS = {"2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25", "2026-07-28"}
MAX_REQUEST_BYTES = 1024 * 1024


def _response(payload, status=200):
    response = JsonResponse(payload, status=status)
    response["MCP-Protocol-Version"] = PROTOCOL_VERSION
    return response


def _error(request_id, code, message, status=200):
    return _response({"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}, status)


def _origin_allowed(request):
    origin = request.headers.get("Origin")
    if not origin:
        return True
    if origin in settings.MCP_ALLOWED_ORIGINS:
        return True
    parsed = urlsplit(origin)
    return parsed.scheme in {"http", "https"} and parsed.netloc == request.get_host()


def _authenticate(request):
    authorization = request.headers.get("Authorization", "")
    if not authorization.startswith("Bearer "):
        return None
    token = authorization[7:].strip()
    if not token:
        return None
    gateway_token = GatewayToken.objects.select_related("user").filter(token_hash=opaque_hash(token)).first()
    if gateway_token and gateway_token.user.is_active:
        return gateway_token.user, frozenset({"mcp:read", "mcp:write"})
    return authenticate_access_token(token, canonical_resource(request))


def _bounded_body(request):
    stream = request.META.get("wsgi.input")
    if stream is None:
        return b""
    limit = MAX_REQUEST_BYTES + 1
    try:
        limit = min(limit, len(stream))
    except TypeError:
        pass
    return stream.read(limit)


@csrf_exempt
@require_POST
def mcp(request):
    if not _origin_allowed(request):
        security_event("mcp_authorization_failed", request, outcome="denied", reason="origin")
        return _error(None, -32000, "Origin is not allowed.", 403)
    authenticated = _authenticate(request)
    if authenticated is None:
        security_event("mcp_authorization_failed", request, outcome="denied", reason="credentials")
        response = _error(None, -32001, "Authentication required.", 401)
        metadata = f"{canonical_base(request)}/.well-known/oauth-protected-resource/mcp/"
        response["WWW-Authenticate"] = f'Bearer realm="mcp", resource_metadata="{metadata}"'
        return response
    user, scopes = authenticated
    if request.content_type != "application/json":
        return _error(None, -32600, "Content-Type must be application/json.", 415)
    try:
        content_length = int(request.headers.get("Content-Length", "0") or 0)
    except ValueError:
        return _error(None, -32600, "Invalid Content-Length.", 400)
    if content_length < 0 or content_length > MAX_REQUEST_BYTES:
        return _error(None, -32600, "Request is too large.", 413)
    body = _bounded_body(request)
    if len(body) > MAX_REQUEST_BYTES:
        return _error(None, -32600, "Request is too large.", 413)
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return _error(None, -32700, "Invalid JSON.")
    if not isinstance(payload, dict) or payload.get("jsonrpc") != "2.0" or not isinstance(payload.get("method"), str):
        return _error(payload.get("id") if isinstance(payload, dict) else None, -32600, "Invalid JSON-RPC request.")
    params = payload.get("params", {})
    if not isinstance(params, dict):
        return _error(payload.get("id"), -32602, "Parameters must be an object.")
    return _dispatch(request, user, scopes, payload.get("id"), payload["method"], params)


def _dispatch(request, user, scopes, request_id, method, params):
    if method == "notifications/initialized":
        return HttpResponse(status=202)
    if request_id is None:
        return _error(None, -32600, "Requests require an id.")
    if method == "initialize":
        requested = params.get("protocolVersion")
        version = requested if requested in SUPPORTED_VERSIONS else PROTOCOL_VERSION
        return _response({
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "protocolVersion": version,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": settings.PRODUCT_NAME, "version": "0.1.0"},
            },
        })
    if method == "ping":
        return _response({"jsonrpc": "2.0", "id": request_id, "result": {}})
    if method == "tools/list":
        return _response({"jsonrpc": "2.0", "id": request_id, "result": {"tools": META_TOOLS}})
    if method == "tools/call":
        name = params.get("name")
        arguments = params.get("arguments", {})
        if not isinstance(name, str) or not isinstance(arguments, dict):
            return _error(request_id, -32602, "Tool name and object arguments are required.")
        try:
            result = execute(user, name, arguments, request, scopes)
            content = [{"type": "text", "text": json.dumps(result, separators=(",", ":"))}]
            return _response({"jsonrpc": "2.0", "id": request_id, "result": {"content": content}})
        except (ValueError, TypeError, ValidationError) as exc:
            content = [{"type": "text", "text": str(exc)}]
            return _response({"jsonrpc": "2.0", "id": request_id, "result": {"content": content, "isError": True}})
    return _error(request_id, -32601, "Method not found.")
