import hmac
import json
import uuid
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.http import Http404, JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from gateway.security import opaque_hash

from .crypto import LicenseDocumentError, sign_document
from .models import IssuedLicense


@csrf_exempt
@require_POST
def validate_license(request):
    if settings.DEPLOYMENT_MODE != "cloud":
        raise Http404
    try:
        payload = json.loads(request.body)
        key = payload["license_key"]
        instance_id = payload["instance_id"]
        if not isinstance(key, str) or not isinstance(instance_id, str):
            raise ValueError
        instance_uuid = uuid.UUID(instance_id)
    except (ValueError, KeyError, TypeError, json.JSONDecodeError):
        return JsonResponse({"error": "Invalid request."}, status=400)
    supplied_hash = opaque_hash(key)
    now = timezone.now()
    with transaction.atomic():
        license_record = IssuedLicense.objects.select_for_update().filter(key_hash=supplied_hash).first()
        stored_hash = license_record.key_hash if license_record else "0" * 64
        hash_matches = hmac.compare_digest(stored_hash, supplied_hash)
        if license_record is None or not hash_matches:
            return JsonResponse({"error": "License is invalid."}, status=403)
        if license_record.revoked_at or license_record.expires_at <= now:
            return JsonResponse({"error": "License is inactive."}, status=403)
        if license_record.bound_instance_id and license_record.bound_instance_id != instance_uuid:
            return JsonResponse({"error": "License is bound to another instance."}, status=403)
        if (
            not license_record.organization.strip()
            or not isinstance(license_record.entitlements, list)
            or not all(isinstance(value, str) for value in license_record.entitlements)
            or not set(license_record.entitlements) <= settings.LICENSE_ENTITLEMENTS
        ):
            return JsonResponse({"error": "License data is invalid."}, status=403)
        issued_at = int(now.timestamp())
        expires_at = int(min(
            license_record.expires_at,
            now + timedelta(seconds=settings.LICENSE_DOCUMENT_MAX_LIFETIME_SECONDS),
        ).timestamp())
        if expires_at <= issued_at:
            return JsonResponse({"error": "License is inactive."}, status=403)
        claims = {
            "iss": settings.LICENSE_ISSUER,
            "aud": settings.LICENSE_AUDIENCE,
            "instance_id": str(instance_uuid),
            "iat": issued_at,
            "exp": expires_at,
            "organization": license_record.organization,
            "entitlements": license_record.entitlements,
        }
        try:
            document = sign_document(claims)
        except LicenseDocumentError:
            return JsonResponse({"error": "License signing is unavailable."}, status=503)
        if not license_record.bound_instance_id:
            license_record.bound_instance_id = instance_uuid
            license_record.save(update_fields=["bound_instance_id"])
    return JsonResponse({"signed_document": document})
