import json
import sys
import urllib.error
import urllib.request
from datetime import timedelta
from urllib.parse import urlsplit

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .crypto import LicenseDocumentError, decrypt_license_key, encrypt_license_key, verify_document
from .models import InstanceLicense


class LicenseServerError(RuntimeError):
    pass


class _LicenseOutage(LicenseServerError):
    pass


class _LicenseRejected(LicenseServerError):
    pass


class _LicenseResponseError(LicenseServerError):
    pass


def _validation_url():
    value = settings.LICENSE_VALIDATION_URL
    parsed = urlsplit(value)
    testing = "test" in sys.argv
    if not value or not parsed.hostname or (parsed.scheme != "https" and not settings.DEBUG and not testing):
        raise LicenseServerError("A valid HTTPS license validation URL is required.")
    return value


def _fetch_document(key, instance_id):
    payload = json.dumps({"license_key": key, "instance_id": str(instance_id)}).encode()
    request = urllib.request.Request(
        _validation_url(), payload, {"Content-Type": "application/json", "Accept": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(request, timeout=settings.LICENSE_VALIDATION_TIMEOUT) as response:
            body = response.read()
    except urllib.error.HTTPError as exc:
        if exc.code >= 500:
            raise _LicenseOutage("License server is unavailable.") from exc
        raise _LicenseRejected("License was rejected by the validation server.") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise _LicenseOutage("License server is unavailable.") from exc
    try:
        document = json.loads(body)["signed_document"]
        if not isinstance(document, str):
            raise ValueError
    except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise _LicenseResponseError("License server returned an invalid response.") from exc
    return document


def _save_valid(instance, encrypted_key, document):
    now = timezone.now()
    instance.encrypted_key = encrypted_key
    instance.signed_document = document
    instance.status = "valid"
    instance.last_error = ""
    instance.validated_at = now
    instance.grace_deadline = now + timedelta(days=settings.LICENSE_GRACE_DAYS)
    instance.save()


def _invalidate(instance, message):
    instance.signed_document = ""
    instance.status = "invalid"
    instance.last_error = message
    instance.save(update_fields=["signed_document", "status", "last_error"])


def refresh(instance=None):
    instance = instance or InstanceLicense.current()
    if not instance.encrypted_key:
        raise LicenseServerError("No license key is installed.")
    try:
        key = decrypt_license_key(instance.encrypted_key)
    except LicenseDocumentError as exc:
        raise LicenseServerError(str(exc)) from exc
    try:
        document = _fetch_document(key, instance.instance_id)
    except _LicenseOutage as exc:
        return _record_outage(instance, exc)
    except (_LicenseRejected, _LicenseResponseError) as exc:
        _invalidate(instance, str(exc))
        raise LicenseServerError(str(exc)) from exc
    try:
        claims = verify_document(document, instance.instance_id)
    except LicenseDocumentError as exc:
        _invalidate(instance, str(exc))
        raise LicenseServerError(str(exc)) from exc
    _save_valid(instance, instance.encrypted_key, document)
    return claims


def _record_outage(instance, exc):
    grace_usable = False
    if instance.signed_document and instance.grace_deadline and timezone.now() <= instance.grace_deadline:
        try:
            verify_document(instance.signed_document, instance.instance_id, allow_expired=True)
            grace_usable = True
        except LicenseDocumentError:
            pass
    instance.status = "grace" if grace_usable else "unavailable"
    instance.last_error = "License server is unavailable."
    instance.save(update_fields=["status", "last_error"])
    raise LicenseServerError(instance.last_error) from exc


def install(key):
    instance = InstanceLicense.current()
    try:
        encrypted_key = encrypt_license_key(key)
        document = _fetch_document(key, instance.instance_id)
        claims = verify_document(document, instance.instance_id)
    except (LicenseDocumentError, ValueError) as exc:
        raise LicenseServerError(str(exc)) from exc
    with transaction.atomic():
        locked = InstanceLicense.objects.select_for_update().get(pk=instance.pk)
        _save_valid(locked, encrypted_key, document)
    return claims


def remove():
    instance = InstanceLicense.current()
    instance.encrypted_key = ""
    instance.signed_document = ""
    instance.status = ""
    instance.last_error = ""
    instance.validated_at = None
    instance.grace_deadline = None
    instance.save()


def usable_claims(instance=None, allow_grace=True):
    instance = instance or InstanceLicense.current()
    if not instance.signed_document:
        return None
    try:
        return verify_document(instance.signed_document, instance.instance_id)
    except LicenseDocumentError:
        if (
            not allow_grace
            or instance.status != "grace"
            or not instance.grace_deadline
            or timezone.now() > instance.grace_deadline
        ):
            return None
        try:
            return verify_document(instance.signed_document, instance.instance_id, allow_expired=True)
        except LicenseDocumentError:
            return None
