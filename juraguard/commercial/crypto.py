import base64
import json
import os
from datetime import datetime, timezone

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from django.conf import settings


class LicenseDocumentError(ValueError):
    pass


def _encryption_keys():
    configured = os.environ.get("LICENSE_ENCRYPTION_KEYS", "")
    if configured:
        keys = [value.strip().encode() for value in configured.split(",") if value.strip()]
        if not keys:
            raise LicenseDocumentError("License encryption key configuration is invalid.")
        return keys
    path = settings.DATA_DIR / "license_encryption_key"
    try:
        key = path.read_bytes().strip()
    except FileNotFoundError:
        key = Fernet.generate_key()
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as key_file:
            key_file.write(key)
    return [key]


def encrypt_license_key(value):
    return Fernet(_encryption_keys()[0]).encrypt(value.encode()).decode()


def decrypt_license_key(value):
    for key in _encryption_keys():
        try:
            return Fernet(key).decrypt(value.encode()).decode()
        except (InvalidToken, ValueError):
            continue
    raise LicenseDocumentError("Stored license key cannot be decrypted; restore its encryption key or replace the license.")


def _decode_key(value, private=False):
    try:
        raw = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
        return Ed25519PrivateKey.from_private_bytes(raw) if private else Ed25519PublicKey.from_public_bytes(raw)
    except (ValueError, TypeError) as exc:
        raise LicenseDocumentError("License signing key configuration is invalid.") from exc


def sign_document(claims):
    if not settings.LICENSE_SIGNING_PRIVATE_KEY:
        raise LicenseDocumentError("License signing private key is not configured.")
    payload = json.dumps(claims, sort_keys=True, separators=(",", ":")).encode()
    encoded = base64.urlsafe_b64encode(payload).rstrip(b"=")
    signature = _decode_key(settings.LICENSE_SIGNING_PRIVATE_KEY, private=True).sign(encoded)
    return f"{encoded.decode()}.{base64.urlsafe_b64encode(signature).rstrip(b'=').decode()}"


def verify_document(document, instance_id, now=None, allow_expired=False):
    if not settings.LICENSE_SIGNING_PUBLIC_KEY:
        raise LicenseDocumentError("License signing public key is not configured.")
    try:
        encoded, encoded_signature = document.split(".", 1)
        signature = base64.urlsafe_b64decode(encoded_signature + "=" * (-len(encoded_signature) % 4))
        _decode_key(settings.LICENSE_SIGNING_PUBLIC_KEY).verify(signature, encoded.encode())
        claims = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
    except Exception as exc:
        raise LicenseDocumentError("Signed license document is invalid.") from exc
    now_timestamp = int((now or datetime.now(timezone.utc)).timestamp())
    required = {"iss", "aud", "instance_id", "iat", "exp", "organization", "entitlements"}
    if not isinstance(claims, dict) or not required.issubset(claims):
        raise LicenseDocumentError("Signed license document is incomplete.")
    if claims["iss"] != settings.LICENSE_ISSUER or claims["aud"] != settings.LICENSE_AUDIENCE:
        raise LicenseDocumentError("Signed license document has the wrong issuer or audience.")
    if claims["instance_id"] != str(instance_id):
        raise LicenseDocumentError("License belongs to another instance.")
    issued_at = claims["iat"]
    expires_at = claims["exp"]
    skew = settings.LICENSE_DOCUMENT_CLOCK_SKEW_SECONDS
    max_lifetime = settings.LICENSE_DOCUMENT_MAX_LIFETIME_SECONDS
    if not isinstance(issued_at, int) or isinstance(issued_at, bool) or issued_at > now_timestamp + skew:
        raise LicenseDocumentError("Signed license issue time is invalid.")
    if not allow_expired and issued_at < now_timestamp - max_lifetime - skew:
        raise LicenseDocumentError("Signed license issue time is too old.")
    if (
        not isinstance(expires_at, int)
        or isinstance(expires_at, bool)
        or expires_at <= issued_at
        or expires_at - issued_at > max_lifetime + skew
    ):
        raise LicenseDocumentError("Signed license validity period is invalid.")
    if expires_at <= now_timestamp and not allow_expired:
        raise LicenseDocumentError("License entitlement document has expired.")
    if not isinstance(claims["organization"], str) or not claims["organization"].strip():
        raise LicenseDocumentError("License organization is invalid.")
    entitlements = claims["entitlements"]
    if not isinstance(entitlements, list) or not all(
        isinstance(value, str) and value in settings.LICENSE_ENTITLEMENTS for value in entitlements
    ):
        raise LicenseDocumentError("License contains unsupported entitlements.")
    return claims
