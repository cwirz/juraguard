import base64
import hashlib
import ipaddress
import json
import re
import socket
from dataclasses import dataclass
from urllib.parse import urlsplit

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured, ValidationError


BLOCKED_UPSTREAM_HEADERS = {
    "accept",
    "accept-encoding",
    "connection",
    "content-length",
    "content-type",
    "host",
    "keep-alive",
    "mcp-protocol-version",
    "mcp-session-id",
    "proxy-authenticate",
    "proxy-authorization",
    "proxy-connection",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}
HEADER_NAME = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")


@dataclass(frozen=True)
class RemoteTarget:
    scheme: str
    hostname: str
    port: int
    address: str
    path: str


def opaque_hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _fernet() -> MultiFernet:
    configured = settings.CREDENTIAL_ENCRYPTION_KEYS
    keys = configured or [base64.urlsafe_b64encode(hashlib.sha256(settings.SECRET_KEY.encode()).digest()).decode()]
    try:
        return MultiFernet([Fernet(key.encode()) for key in keys])
    except (TypeError, ValueError) as exc:
        raise ImproperlyConfigured("CREDENTIAL_ENCRYPTION_KEYS contains an invalid Fernet key.") from exc


def encrypt_value(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return _fernet().encrypt(payload).decode()


def decrypt_value(value: str, default=None):
    if not value:
        return default
    try:
        return json.loads(_fernet().decrypt(value.encode()))
    except (InvalidToken, json.JSONDecodeError) as exc:
        raise ValueError("Stored credentials cannot be decrypted.") from exc


def encrypt_headers(headers: dict[str, str]) -> str:
    return encrypt_value(headers)


def decrypt_headers(value: str) -> dict[str, str]:
    payload = decrypt_value(value, {})
    if not isinstance(payload, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in payload.items()):
        raise ValueError("Stored credentials are invalid.")
    return payload


def _parsed_remote_url(value: str):
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise ValidationError("Enter a valid remote MCP URL.") from exc
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValidationError("Remote MCP URL must use HTTP or HTTPS.")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValidationError("Remote MCP URL cannot contain credentials, a query string, or a fragment.")
    if parsed.scheme != "https" and not _private_networks_allowed():
        raise ValidationError("HTTPS is required unless private networks are enabled.")
    return parsed, port or (443 if parsed.scheme == "https" else 80)


def resolve_remote_target(value: str) -> RemoteTarget:
    parsed, port = _parsed_remote_url(value)
    try:
        addresses = list(dict.fromkeys(
            item[4][0] for item in socket.getaddrinfo(parsed.hostname, port, type=socket.SOCK_STREAM)
        ))
    except socket.gaierror as exc:
        raise ValidationError("Remote MCP hostname could not be resolved.") from exc
    if not addresses:
        raise ValidationError("Remote MCP hostname could not be resolved.")
    if not _private_networks_allowed():
        for address in addresses:
            ip = ipaddress.ip_address(address)
            if not ip.is_global:
                raise ValidationError("Remote MCP URL resolves to a blocked network address.")
    return RemoteTarget(parsed.scheme, parsed.hostname, port, addresses[0], parsed.path or "/")


def _private_networks_allowed():
    return settings.DEPLOYMENT_MODE == "self_hosted" and settings.ALLOW_PRIVATE_NETWORKS


def validate_remote_url(value: str, *, resolve: bool = True) -> str:
    if resolve:
        resolve_remote_target(value)
    else:
        _parsed_remote_url(value)
    return value


def validate_remote_url_syntax(value: str) -> None:
    validate_remote_url(value, resolve=False)


def clean_secret_headers(value: object) -> dict[str, str]:
    if not isinstance(value, dict) or not value:
        raise ValidationError("Enter at least one header as a JSON object.")
    headers: dict[str, str] = {}
    names: set[str] = set()
    for raw_name, raw_value in value.items():
        if not isinstance(raw_name, str) or not isinstance(raw_value, str):
            raise ValidationError("Header names and values must be text.")
        name = raw_name.strip()
        lower_name = name.lower()
        if not HEADER_NAME.fullmatch(name) or lower_name in BLOCKED_UPSTREAM_HEADERS:
            raise ValidationError("Header names are invalid.")
        if lower_name in names:
            raise ValidationError("Header names must be unique.")
        if "\r" in raw_value or "\n" in raw_value:
            raise ValidationError("Header values cannot contain new lines.")
        names.add(lower_name)
        headers[name] = raw_value
    return headers
