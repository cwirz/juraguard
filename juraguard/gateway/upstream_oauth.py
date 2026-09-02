import base64
import fcntl
import hashlib
import ipaddress
import os
import re
import secrets
from datetime import timedelta
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from django.conf import settings
from django.shortcuts import redirect
from django.utils import timezone

from .models import Integration
from .oauth_server import canonical_base
from .outbound import OutboundError, request


RESOURCE_METADATA = re.compile(r'resource_metadata="([^"]+)"')


class UpstreamOAuthError(Exception):
    pass


def _well_known(resource_url):
    parsed = urlsplit(resource_url)
    path = "/.well-known/oauth-protected-resource" + (parsed.path if parsed.path != "/" else "")
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def _as_metadata_url(issuer):
    parsed = urlsplit(issuer)
    suffix = parsed.path.rstrip("/")
    return urlunsplit((parsed.scheme, parsed.netloc, f"/.well-known/oauth-authorization-server{suffix}", "", ""))


def _oidc_insertion_metadata_url(issuer):
    parsed = urlsplit(issuer)
    suffix = parsed.path.rstrip("/")
    return urlunsplit((parsed.scheme, parsed.netloc, f"/.well-known/openid-configuration{suffix}", "", ""))


def _oidc_appending_metadata_url(issuer):
    parsed = urlsplit(issuer)
    path = f"{parsed.path.rstrip('/')}/.well-known/openid-configuration"
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


def _absolute_https_url(value, label):
    try:
        parsed = urlsplit(value)
        parsed.port
    except (TypeError, ValueError) as exc:
        raise UpstreamOAuthError(f"Invalid {label}.") from exc
    try:
        local_http = (
            parsed.scheme == "http"
            and settings.ALLOW_PRIVATE_NETWORKS
            and (parsed.hostname == "localhost" or ipaddress.ip_address(parsed.hostname).is_loopback)
        )
    except ValueError:
        local_http = False
    if (
        (parsed.scheme != "https" and not local_http)
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.fragment
    ):
        raise UpstreamOAuthError(f"Invalid {label}.")
    return value


def discover(integration):
    resource_url = integration.remote_url
    metadata_url = _well_known(resource_url)
    try:
        probe = request(
            resource_url,
            method="POST",
            json_body={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            allowed_status=(401,),
        )
        if match := RESOURCE_METADATA.search(probe.headers.get("www-authenticate", "")):
            metadata_url = match.group(1)
        resource_metadata = request(_absolute_https_url(metadata_url, "resource metadata URL")).json()
        if resource_metadata.get("resource") != resource_url:
            raise UpstreamOAuthError("Protected resource metadata does not match the MCP URL.")
        servers = resource_metadata.get("authorization_servers")
        if not isinstance(servers, list) or len(servers) != 1:
            raise UpstreamOAuthError("Protected resource metadata must name one authorization server.")
        issuer = _absolute_https_url(servers[0], "authorization server issuer")
        discovery_urls = (
            _as_metadata_url(issuer),
            _oidc_insertion_metadata_url(issuer),
            _oidc_appending_metadata_url(issuer),
        )
        metadata = None
        for discovery_url in discovery_urls:
            response = request(discovery_url, allowed_status=(200, 404))
            if response.status != 404:
                metadata = response.json()
                break
        if metadata is None:
            raise UpstreamOAuthError("Authorization server metadata was not found.")
    except OutboundError as exc:
        raise UpstreamOAuthError(str(exc)) from exc
    if metadata.get("issuer") != issuer:
        raise UpstreamOAuthError("Authorization server issuer does not match discovery URL.")
    if "S256" not in metadata.get("code_challenge_methods_supported", []):
        raise UpstreamOAuthError("Authorization server does not support PKCE S256.")
    return {
        "issuer": issuer,
        "authorization_endpoint": _absolute_https_url(metadata.get("authorization_endpoint"), "authorization endpoint"),
        "token_endpoint": _absolute_https_url(metadata.get("token_endpoint"), "token endpoint"),
        "registration_endpoint": metadata.get("registration_endpoint", ""),
        "client_id_metadata_document_supported": bool(metadata.get("client_id_metadata_document_supported")),
        "authorization_response_iss_parameter_supported": bool(
            metadata.get("authorization_response_iss_parameter_supported")
        ),
    }


def _client_urls(request, integration):
    base = canonical_base(request)
    return (
        f"{base}/oauth/upstream-client/{integration.pk}/{integration.client_metadata_token()}/",
        f"{base}/integrations/{integration.pk}/oauth/callback/",
    )


def client_metadata(request, integration):
    client_id, callback = _client_urls(request, integration)
    return {
        "client_id": client_id,
        "client_name": "Juraguard MCP Gateway",
        "redirect_uris": [callback],
        "token_endpoint_auth_method": "none",
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
    }


def _register_client(metadata, request_obj, integration):
    client_id, callback = _client_urls(request_obj, integration)
    if metadata["client_id_metadata_document_supported"]:
        return client_id
    endpoint = metadata.get("registration_endpoint")
    if not endpoint:
        raise UpstreamOAuthError("Authorization server supports neither client metadata documents nor registration.")
    try:
        response = request(
            _absolute_https_url(endpoint, "registration endpoint"),
            method="POST",
            json_body={
                "client_name": "Juraguard MCP Gateway",
                "redirect_uris": [callback],
                "token_endpoint_auth_method": "none",
                "grant_types": ["authorization_code", "refresh_token"],
                "response_types": ["code"],
            },
            allowed_status=(200, 201),
        ).json()
    except OutboundError as exc:
        raise UpstreamOAuthError(str(exc)) from exc
    if response.get("client_secret") or not isinstance(response.get("client_id"), str):
        raise UpstreamOAuthError("Authorization server did not register a public client.")
    return response["client_id"]


def start(request_obj, integration):
    metadata = discover(integration)
    client_id = _register_client(metadata, request_obj, integration)
    _, callback = _client_urls(request_obj, integration)
    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    request_obj.session[f"upstream_oauth_{integration.pk}"] = {
        "state": state,
        "verifier": verifier,
        "client_id": client_id,
        "metadata": metadata,
    }
    parameters = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": callback,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "resource": integration.remote_url,
    }
    endpoint = urlsplit(metadata["authorization_endpoint"])
    existing = [(key, value) for key, value in parse_qsl(endpoint.query, keep_blank_values=True) if key not in parameters]
    query = urlencode([*existing, *parameters.items()])
    return redirect(urlunsplit((endpoint.scheme, endpoint.netloc, endpoint.path, query, "")))


def _validated_token(payload, previous_refresh=None):
    if payload.get("token_type", "").lower() != "bearer" or not isinstance(payload.get("access_token"), str):
        raise UpstreamOAuthError("Authorization server returned an invalid token response.")
    try:
        expires_in = int(payload.get("expires_in", 3600))
    except (TypeError, ValueError) as exc:
        raise UpstreamOAuthError("Authorization server returned an invalid token response.") from exc
    if expires_in < 1 or expires_in > 86400:
        raise UpstreamOAuthError("Authorization server returned an invalid token lifetime.")
    refresh_token = payload.get("refresh_token", previous_refresh)
    if refresh_token is not None and not isinstance(refresh_token, str):
        raise UpstreamOAuthError("Authorization server returned an invalid refresh token.")
    return payload["access_token"], refresh_token, timezone.now() + timedelta(seconds=expires_in)


def callback(request_obj, integration):
    session_key = f"upstream_oauth_{integration.pk}"
    pending = request_obj.session.pop(session_key, None)
    if not pending or not secrets.compare_digest(pending["state"], request_obj.GET.get("state", "")):
        raise UpstreamOAuthError("OAuth state is invalid or expired.")
    response_issuer = request_obj.GET.get("iss")
    expected_issuer = pending["metadata"]["issuer"]
    if pending["metadata"].get("authorization_response_iss_parameter_supported") and not response_issuer:
        raise UpstreamOAuthError("Authorization response issuer is missing.")
    if response_issuer is not None and response_issuer != expected_issuer:
        raise UpstreamOAuthError("Authorization response issuer does not match.")
    if request_obj.GET.get("error"):
        raise UpstreamOAuthError("Authorization was denied by the remote server.")
    code = request_obj.GET.get("code")
    if not code:
        raise UpstreamOAuthError("Authorization code is missing.")
    _, callback_url = _client_urls(request_obj, integration)
    metadata = pending["metadata"]
    try:
        payload = request(
            metadata["token_endpoint"],
            method="POST",
            form={
                "grant_type": "authorization_code",
                "code": code,
                "client_id": pending["client_id"],
                "redirect_uri": callback_url,
                "code_verifier": pending["verifier"],
                "resource": integration.remote_url,
            },
        ).json()
    except OutboundError as exc:
        raise UpstreamOAuthError(str(exc)) from exc
    access, refresh, expires_at = _validated_token(payload)
    integration.set_oauth_state({
        "access_token": access,
        "refresh_token": refresh,
        "expires_at": expires_at.isoformat(),
        "client_id": pending["client_id"],
        "token_endpoint": metadata["token_endpoint"],
        "issuer": metadata["issuer"],
        "resource": integration.remote_url,
    })
    integration.save(update_fields=["encrypted_oauth_state", "updated_at"])


def authorization_headers(integration):
    # ponytail: host file lock matches Juraguard's single-node SQLite deployment.
    descriptor = os.open(settings.DATA_DIR / f"oauth-refresh-{integration.pk}.lock", os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        locked = Integration.objects.get(pk=integration.pk)
        state = locked.get_oauth_state()
        if not state.get("access_token"):
            raise UpstreamOAuthError("OAuth connection is incomplete.")
        expires_at = timezone.datetime.fromisoformat(state["expires_at"])
        if expires_at > timezone.now() + timedelta(seconds=30):
            return {"Authorization": f"Bearer {state['access_token']}"}
        if not state.get("refresh_token"):
            raise UpstreamOAuthError("Remote OAuth session expired; reconnect it.")
        try:
            payload = request(
                state["token_endpoint"],
                method="POST",
                form={
                    "grant_type": "refresh_token",
                    "refresh_token": state["refresh_token"],
                    "client_id": state["client_id"],
                    "resource": state["resource"],
                },
            ).json()
        except OutboundError as exc:
            raise UpstreamOAuthError(str(exc)) from exc
        access, refresh, expiry = _validated_token(payload, state["refresh_token"])
        state.update(access_token=access, refresh_token=refresh, expires_at=expiry.isoformat())
        locked.set_oauth_state(state)
        locked.save(update_fields=["encrypted_oauth_state", "updated_at"])
        integration.encrypted_oauth_state = locked.encrypted_oauth_state
        return {"Authorization": f"Bearer {access}"}
    finally:
        os.close(descriptor)
