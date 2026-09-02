import base64
import hashlib
import ipaddress
import json
import re
import secrets
import uuid
from datetime import timedelta
from urllib.parse import urlencode, urlsplit

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.exceptions import RequestDataTooBig, TooManyFieldsSent
from django.db import transaction
from django.http import JsonResponse
from django.http.multipartparser import MultiPartParserError
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods, require_POST

from .models import OAuthAccessToken, OAuthAuthorizationCode, OAuthClient, OAuthRefreshToken
from .hardening import security_event
from .outbound import OutboundError, request as outbound_request
from .security import opaque_hash


CODE_LIFETIME = timedelta(minutes=5)
ACCESS_LIFETIME = timedelta(hours=1)
REFRESH_LIFETIME = timedelta(days=30)
DYNAMIC_CLIENT_LIFETIME = timedelta(days=30)
UNUSED_DYNAMIC_CLIENT_LIFETIME = timedelta(minutes=10)
MAX_MACHINE_BODY = 64 * 1024
PKCE_VALUE = re.compile(r"^[A-Za-z0-9._~-]{43,128}$")
SUPPORTED_SCOPES = ("mcp:read", "mcp:write")
DEFAULT_SCOPE = "mcp:read"


def canonical_base(request):
    if settings.PUBLIC_BASE_URL:
        base = settings.PUBLIC_BASE_URL.rstrip("/")
        try:
            parsed = urlsplit(base)
            parsed.port
        except ValueError as exc:
            raise ValueError("PUBLIC_BASE_URL must be an HTTPS origin.") from exc
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.path
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("PUBLIC_BASE_URL must be an HTTPS origin.")
        return base
    host = urlsplit(f"//{request.get_host()}").hostname
    try:
        local = host in {"localhost", "testserver"} or ipaddress.ip_address(host).is_loopback
    except (TypeError, ValueError):
        local = False
    if not local:
        raise ValueError("PUBLIC_BASE_URL is required outside localhost.")
    return request.build_absolute_uri("/").rstrip("/")


def canonical_resource(request):
    return f"{canonical_base(request)}/mcp/"


def _json(payload, status=200):
    response = JsonResponse(payload, status=status)
    response["Cache-Control"] = "no-store"
    response["Pragma"] = "no-cache"
    return response


def protected_resource_metadata(request):
    base = canonical_base(request)
    return _json({
        "resource": f"{base}/mcp/",
        "authorization_servers": [base],
        "bearer_methods_supported": ["header"],
        "scopes_supported": list(SUPPORTED_SCOPES),
    })


def authorization_server_metadata(request):
    base = canonical_base(request)
    return _json({
        "issuer": base,
        "authorization_endpoint": f"{base}/oauth/authorize/",
        "token_endpoint": f"{base}/oauth/token/",
        "registration_endpoint": f"{base}/oauth/register/",
        "revocation_endpoint": f"{base}/oauth/revoke/",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "token_endpoint_auth_methods_supported": ["none"],
        "code_challenge_methods_supported": ["S256"],
        "scopes_supported": list(SUPPORTED_SCOPES),
        "client_id_metadata_document_supported": True,
    })


def _valid_redirect(uri):
    try:
        parsed = urlsplit(uri)
        parsed.port
    except (TypeError, ValueError):
        return False
    if parsed.fragment or parsed.username or parsed.password or not parsed.hostname:
        return False
    if parsed.scheme == "https":
        return True
    if parsed.scheme != "http":
        return False
    try:
        return parsed.hostname == "localhost" or ipaddress.ip_address(parsed.hostname).is_loopback
    except ValueError:
        return False


def _localhost(host):
    try:
        return host == "localhost" or ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _client_payload(value, *, require_name=False):
    if not isinstance(value, dict):
        raise ValueError("Invalid client metadata.")
    redirects = value.get("redirect_uris")
    if not isinstance(redirects, list) or not redirects or len(redirects) > 10:
        raise ValueError("Invalid redirect URIs.")
    if not all(isinstance(uri, str) and len(uri) <= 1000 and _valid_redirect(uri) for uri in redirects):
        raise ValueError("Invalid redirect URIs.")
    if value.get("token_endpoint_auth_method", "none") != "none":
        raise ValueError("Only public clients are supported.")
    grants = value.get("grant_types", ["authorization_code"])
    responses = value.get("response_types", ["code"])
    if (
        not isinstance(grants, list)
        or not all(isinstance(item, str) for item in grants)
        or set(grants) - {"authorization_code", "refresh_token"}
    ):
        raise ValueError("Unsupported grant type.")
    if (
        not isinstance(responses, list)
        or not all(isinstance(item, str) for item in responses)
        or set(responses) != {"code"}
    ):
        raise ValueError("Unsupported response type.")
    name = value.get("client_name", "")
    if not isinstance(name, str) or (require_name and not name.strip()):
        raise ValueError("Invalid client name.")
    return redirects, name.strip()[:200]


def _resolve_client(client_id):
    parsed = urlsplit(client_id)
    if parsed.scheme != "https":
        client = OAuthClient.objects.filter(client_id=client_id).first()
        if not client or (client.is_dynamic and client.expires_at <= timezone.now()):
            raise ValueError("Unknown client.")
        if client.is_dynamic:
            client.expires_at = timezone.now() + DYNAMIC_CLIENT_LIFETIME
            client.save(update_fields=["expires_at"])
        return client
    if not parsed.hostname or not parsed.path or parsed.path == "/" or parsed.username or parsed.password or parsed.fragment:
        raise ValueError("Unknown client.")
    try:
        payload = outbound_request(client_id).json()
    except OutboundError as exc:
        raise ValueError("Client metadata could not be verified.") from exc
    if payload.get("client_id") != client_id:
        raise ValueError("Client metadata client_id does not match.")
    redirects, name = _client_payload(payload, require_name=True)
    client, _ = OAuthClient.objects.update_or_create(
        client_id=client_id,
        defaults={"client_name": name, "redirect_uris": redirects, "is_dynamic": False, "expires_at": None},
    )
    return client


def _machine_body(request):
    try:
        length = int(request.headers.get("Content-Length", "0") or 0)
    except ValueError as exc:
        raise ValueError("Invalid Content-Length.") from exc
    if length < 0 or length > MAX_MACHINE_BODY:
        raise ValueError("Request body is too large.")
    try:
        body = request.body
    except RequestDataTooBig as exc:
        raise ValueError("Request body is too large.") from exc
    if len(body) > MAX_MACHINE_BODY:
        raise ValueError("Request body is too large.")
    return body


def _machine_form(request):
    _machine_body(request)
    try:
        return request.POST
    except (MultiPartParserError, RequestDataTooBig, TooManyFieldsSent) as exc:
        raise ValueError("Invalid form body.") from exc


def _normalize_scope(value=DEFAULT_SCOPE):
    if not isinstance(value, str):
        raise ValueError("Invalid scope.")
    requested = value.split(" ")
    if not requested or any(not item or item not in SUPPORTED_SCOPES for item in requested):
        raise ValueError("Invalid scope.")
    return " ".join(scope for scope in SUPPORTED_SCOPES if scope in set(requested))


@csrf_exempt
@require_POST
def register(request):
    try:
        if request.content_type != "application/json":
            raise ValueError("Content-Type must be application/json.")
        value = json.loads(_machine_body(request).decode())
        redirects, name = _client_payload(value)
    except (UnicodeDecodeError, ValueError) as exc:
        return _json({"error": "invalid_client_metadata", "error_description": str(exc)}, 400)
    now = timezone.now()
    with transaction.atomic():
        OAuthClient.objects.filter(is_dynamic=True, expires_at__lte=now).delete()
        client = OAuthClient.objects.filter(
            is_dynamic=True, expires_at__gt=now, client_name=name, redirect_uris=redirects,
        ).first()
        if client:
            client.expires_at = now + DYNAMIC_CLIENT_LIFETIME
            client.save(update_fields=["expires_at"])
        else:
            client = OAuthClient.objects.create(
                client_id=f"mcp_{secrets.token_urlsafe(24)}",
                client_name=name,
                redirect_uris=redirects,
                is_dynamic=True,
                expires_at=now + UNUSED_DYNAMIC_CLIENT_LIFETIME,
            )
    return _json({
        "client_id": client.client_id,
        "client_name": name,
        "redirect_uris": redirects,
        "token_endpoint_auth_method": "none",
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
    }, 201)


def _authorization_parameters(request):
    source = request.POST if request.method == "POST" else request.GET
    required = ("client_id", "redirect_uri", "resource", "code_challenge")
    if any(not source.get(name) for name in required):
        raise ValueError("Required authorization parameters are missing.")
    if source.get("response_type") != "code" or source.get("code_challenge_method") != "S256":
        raise ValueError("Authorization code with PKCE S256 is required.")
    scope = _normalize_scope(source["scope"] if "scope" in source else DEFAULT_SCOPE)
    if source["resource"] != canonical_resource(request):
        raise ValueError("Invalid resource.")
    client = _resolve_client(source["client_id"])
    if source["redirect_uri"] not in client.redirect_uris:
        raise ValueError("Invalid redirect URI.")
    challenge = source["code_challenge"]
    if not PKCE_VALUE.fullmatch(challenge):
        raise ValueError("Invalid PKCE challenge.")
    return source, client, scope


@login_required
@require_http_methods(["GET", "POST"])
def authorize(request):
    try:
        params, client, scope = _authorization_parameters(request)
    except ValueError as exc:
        return render(request, "gateway/oauth_error.html", {"message": str(exc)}, status=400)
    if request.method == "GET":
        redirect_uri = params["redirect_uri"]
        redirect_host = urlsplit(redirect_uri).hostname
        return render(request, "gateway/oauth_approve.html", {
            "params": params,
            "client": client,
            "redirect_uri": redirect_uri,
            "redirect_host": redirect_host,
            "localhost_redirect": _localhost(redirect_host),
            "scope": scope,
            "permissions": [
                description for name, description in (
                    ("mcp:read", "Use gateway discovery and explicitly read-only integration tools"),
                    ("mcp:write", "Create, change, reconnect, and delete integrations, and use write tools"),
                ) if name in scope.split()
            ],
        })
    if request.POST.get("decision") != "approve":
        query = urlencode({key: value for key, value in {
            "error": "access_denied", "state": params.get("state"),
        }.items() if value})
        separator = "&" if "?" in params["redirect_uri"] else "?"
        return _redirect_exact(f"{params['redirect_uri']}{separator}{query}")
    raw_code = secrets.token_urlsafe(32)
    OAuthAuthorizationCode.objects.create(
        user=request.user,
        client=client,
        code_hash=opaque_hash(raw_code),
        redirect_uri=params["redirect_uri"],
        resource=params["resource"],
        code_challenge=params["code_challenge"],
        scope=scope,
        expires_at=timezone.now() + CODE_LIFETIME,
    )
    separator = "&" if "?" in params["redirect_uri"] else "?"
    query = urlencode({key: value for key, value in {
        "code": raw_code, "state": params.get("state"),
    }.items() if value})
    return _redirect_exact(f"{params['redirect_uri']}{separator}{query}")


def _redirect_exact(url):
    from django.http import HttpResponseRedirect
    response = HttpResponseRedirect(url)
    response["Cache-Control"] = "no-store"
    return response


def _pkce(verifier):
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def _issue_tokens(user, client, resource, scope, family_id=None):
    access = f"mcpa_{secrets.token_urlsafe(32)}"
    refresh = f"mcpr_{secrets.token_urlsafe(32)}"
    now = timezone.now()
    family_id = family_id or uuid.uuid4()
    OAuthAccessToken.objects.create(
        user=user, client=client, token_hash=opaque_hash(access), resource=resource,
        family_id=family_id, scope=scope, expires_at=now + ACCESS_LIFETIME,
    )
    refresh_row = OAuthRefreshToken(
        user=user, client=client, token_hash=opaque_hash(refresh), resource=resource,
        scope=scope, expires_at=now + REFRESH_LIFETIME,
    )
    refresh_row.family_id = family_id
    refresh_row.save()
    return _json({
        "access_token": access,
        "token_type": "Bearer",
        "expires_in": int(ACCESS_LIFETIME.total_seconds()),
        "refresh_token": refresh,
        "scope": scope,
    })


def _invalid_grant():
    return _json({"error": "invalid_grant"}, 400)


@csrf_exempt
@require_POST
def token(request):
    try:
        form = _machine_form(request)
    except ValueError as exc:
        return _json({"error": "invalid_request", "error_description": str(exc)}, 400)
    grant_type = form.get("grant_type")
    if grant_type == "authorization_code":
        response = _exchange_code(form)
    elif grant_type == "refresh_token":
        response = _exchange_refresh(form)
    else:
        response = _json({"error": "unsupported_grant_type"}, 400)
    if response.status_code >= 400:
        security_event("mcp_oauth_failure", request, outcome="denied", reason="invalid_grant")
    return response


def _exchange_code(form):
    now = timezone.now()
    verifier = form.get("code_verifier", "")
    if not PKCE_VALUE.fullmatch(verifier):
        return _invalid_grant()
    try:
        requested_scope = _normalize_scope(form["scope"]) if "scope" in form else None
    except ValueError:
        return _invalid_grant()
    code_hash = opaque_hash(form.get("code", ""))
    with transaction.atomic():
        code = OAuthAuthorizationCode.objects.select_for_update().select_related("client", "user").filter(
            code_hash=code_hash,
            used_at__isnull=True,
            expires_at__gt=now,
            client__client_id=form.get("client_id"),
            redirect_uri=form.get("redirect_uri"),
            resource=form.get("resource"),
            code_challenge=_pkce(verifier),
        ).first()
        if code is None:
            return _invalid_grant()
        scope = requested_scope or code.scope
        if not set(scope.split()).issubset(code.scope.split()):
            return _invalid_grant()
        code.used_at = now
        code.save(update_fields=["used_at"])
        return _issue_tokens(code.user, code.client, code.resource, scope)


def _exchange_refresh(form):
    now = timezone.now()
    token_hash = opaque_hash(form.get("refresh_token", ""))
    try:
        requested_scope = _normalize_scope(form["scope"]) if "scope" in form else None
    except ValueError:
        return _json({"error": "invalid_scope"}, 400)
    with transaction.atomic():
        refresh = OAuthRefreshToken.objects.select_for_update().select_related("client", "user").filter(
            token_hash=token_hash,
        ).first()
        if refresh and (refresh.used_at or refresh.revoked_at):
            OAuthRefreshToken.objects.filter(family_id=refresh.family_id).update(revoked_at=now)
            OAuthAccessToken.objects.filter(family_id=refresh.family_id, revoked_at__isnull=True).update(revoked_at=now)
        if (
            refresh is None
            or refresh.used_at
            or refresh.revoked_at
            or refresh.expires_at <= now
            or refresh.client.client_id != form.get("client_id")
            or refresh.resource != form.get("resource")
        ):
            return _invalid_grant()
        scope = requested_scope or refresh.scope
        if not set(scope.split()).issubset(refresh.scope.split()):
            return _json({"error": "invalid_scope"}, 400)
        refresh.used_at = now
        refresh.save(update_fields=["used_at"])
        if refresh.client.is_dynamic:
            OAuthClient.objects.filter(pk=refresh.client_id).update(expires_at=now + DYNAMIC_CLIENT_LIFETIME)
        return _issue_tokens(refresh.user, refresh.client, refresh.resource, scope, refresh.family_id)


@csrf_exempt
@require_POST
def revoke(request):
    try:
        form = _machine_form(request)
    except ValueError as exc:
        return _json({"error": "invalid_request", "error_description": str(exc)}, 400)
    token_hash = opaque_hash(form.get("token", ""))
    now = timezone.now()
    access = OAuthAccessToken.objects.filter(token_hash=token_hash).first()
    if access:
        access.revoked_at = now
        access.save(update_fields=["revoked_at"])
    refresh = OAuthRefreshToken.objects.filter(token_hash=token_hash).first()
    if refresh:
        OAuthRefreshToken.objects.filter(family_id=refresh.family_id).update(revoked_at=now)
        OAuthAccessToken.objects.filter(family_id=refresh.family_id).update(revoked_at=now)
    return _json({})


def authenticate_access_token(raw_token, resource):
    token = OAuthAccessToken.objects.select_related("user").filter(
        token_hash=opaque_hash(raw_token), resource=resource, revoked_at__isnull=True, expires_at__gt=timezone.now(),
    ).first()
    return (token.user, frozenset(token.scope.split())) if token and token.user.is_active else None
