import hashlib
import hmac
import json
import logging
import time
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, OperationalError
from django.db.models import F
from django.http import HttpResponse, JsonResponse
from django.urls import resolve
from django.utils import timezone

from .models import RateLimitBucket


security_logger = logging.getLogger("juraguard.security")
access_logger = logging.getLogger("juraguard.access")

RATE_LIMITS = {
    "login": (10, 300),
    "signup": (5, 3600),
    "oauth_start": (30, 300),
    "oauth_callback": (30, 300),
    "oauth_token": (60, 60),
    "mcp": (240, 60),
    "credential_setup": (10, 300),
}
_next_prune_at = 0


def _digest(value):
    return hmac.new(settings.SECRET_KEY.encode(), str(value).encode(), hashlib.sha256).hexdigest()


def client_address(request):
    if settings.TRUST_PROXY_HEADERS:
        forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
        if forwarded:
            return forwarded.split(",", 1)[0].strip()
    return request.META.get("REMOTE_ADDR", "unknown")


def security_event(event, request=None, *, workspace=None, integration=None, outcome=None, reason=None):
    payload = {"event": event}
    if request is not None:
        payload["client"] = _digest(client_address(request))
        if request.user.is_authenticated:
            payload["actor"] = _digest(request.user.pk)
    if workspace is not None:
        payload["workspace"] = _digest(workspace.pk)
    if integration is not None:
        payload["integration"] = _digest(integration.pk)
    if outcome:
        payload["outcome"] = outcome
    if reason:
        payload["reason"] = reason
    security_logger.info(json.dumps(payload, sort_keys=True, separators=(",", ":")))


def check_rate_limit(scope, identifier, now=None):
    _prune_expired_buckets()
    limit, seconds = RATE_LIMITS[scope]
    timestamp = int(now if now is not None else time.time())
    window = timestamp // seconds
    lookup = {"scope": scope, "identifier_hash": _digest(identifier), "window": window}
    count = _increment_bucket(lookup)
    return count <= limit, seconds - timestamp % seconds


def _increment_bucket(lookup):
    for attempt in range(5):
        try:
            updated = RateLimitBucket.objects.filter(**lookup).update(count=F("count") + 1, updated_at=timezone.now())
        except OperationalError:
            if attempt == 4:
                raise
            time.sleep(0.01 * (attempt + 1))
            continue
        if updated:
            return RateLimitBucket.objects.values_list("count", flat=True).get(**lookup)
        try:
            RateLimitBucket.objects.create(**lookup, count=1)
            return 1
        except IntegrityError:
            continue
        except OperationalError:
            if attempt == 4:
                raise
            time.sleep(0.01 * (attempt + 1))
    raise RuntimeError("Rate-limit bucket could not be updated.")


def _prune_expired_buckets():
    global _next_prune_at
    monotonic_now = time.monotonic()
    if monotonic_now < _next_prune_at:
        return
    _next_prune_at = monotonic_now + 3600
    try:
        RateLimitBucket.objects.filter(updated_at__lt=timezone.now() - timedelta(hours=2)).delete()
    except OperationalError:
        _next_prune_at = monotonic_now + 60


def _scope(request):
    try:
        name = resolve(request.path_info).url_name
    except Exception:
        return None
    if name in {"login", "account_login"} and request.method == "POST":
        return "login"
    if name in {"account_signup", "owner_setup"} and request.method == "POST":
        return "signup"
    if name == "oauth_authorize":
        return "oauth_start"
    if name in {"oauth_register", "integration_create", "integration_reconnect"} and request.method == "POST":
        return "oauth_start"
    if name == "upstream_oauth_callback" or request.path_info.endswith("/login/callback/"):
        return "oauth_callback"
    if name in {"oauth_token", "oauth_revoke"}:
        return "oauth_token"
    if name == "mcp":
        return "mcp"
    if name == "credential_setup":
        return "credential_setup"
    if request.path_info.startswith("/accounts/") and request.path_info.endswith("/login/"):
        return "oauth_start"
    return None


class SecurityMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        scope = _scope(request)
        if scope:
            allowed, retry_after = check_rate_limit(scope, client_address(request))
            if not allowed:
                security_event("rate_limited", request, outcome="blocked", reason=scope)
                response = self._limited_response(scope)
                response["Retry-After"] = str(retry_after)
                return response
        response = self.get_response(request)
        route = getattr(request.resolver_match, "url_name", None) or "unmatched"
        access_logger.info(json.dumps({
            "client": _digest(client_address(request)),
            "method": request.method,
            "route": route,
            "status": response.status_code,
        }, sort_keys=True, separators=(",", ":")))
        return response

    @staticmethod
    def _limited_response(scope):
        if scope == "mcp":
            return JsonResponse({
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32002, "message": "Too many requests."},
            }, status=429)
        if scope == "oauth_token":
            return JsonResponse({"error": "temporarily_unavailable"}, status=429)
        return HttpResponse("Too many requests. Try again later.", status=429, content_type="text/plain")
