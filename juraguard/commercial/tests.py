import base64
import io
import json
import os
import uuid
import urllib.error
from datetime import timedelta
from unittest.mock import patch

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from gateway.security import opaque_hash

from .crypto import LicenseDocumentError, sign_document, verify_document
from .models import InstanceLicense, IssuedLicense
from .service import LicenseServerError, install, refresh, usable_claims


PRIVATE_KEY = Ed25519PrivateKey.generate()
PRIVATE = base64.urlsafe_b64encode(PRIVATE_KEY.private_bytes(
    serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption()
)).decode()
PUBLIC = base64.urlsafe_b64encode(PRIVATE_KEY.public_key().public_bytes(
    serialization.Encoding.Raw, serialization.PublicFormat.Raw
)).decode()
SIGNING = override_settings(
    LICENSE_SIGNING_PRIVATE_KEY=PRIVATE,
    LICENSE_SIGNING_PUBLIC_KEY=PUBLIC,
    LICENSE_ISSUER="juraguard",
    LICENSE_AUDIENCE="juraguard-self-hosted",
    LICENSE_ENTITLEMENTS={"organization_controls"},
)


def claims(instance_id, expires=None, issued=None):
    now = issued or timezone.now()
    return {
        "iss": "juraguard",
        "aud": "juraguard-self-hosted",
        "instance_id": str(instance_id),
        "iat": int(now.timestamp()),
        "exp": int((expires or now + timedelta(hours=1)).timestamp()),
        "organization": "Example AG",
        "entitlements": ["organization_controls"],
    }


class Response:
    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def read(self):
        return self.body


@SIGNING
class LicenseCryptoTests(TestCase):
    def test_signed_document_rejects_wrong_instance_expiry_and_bad_signature(self):
        instance_id = uuid.uuid4()
        document = sign_document(claims(instance_id))
        self.assertEqual(verify_document(document, instance_id)["organization"], "Example AG")
        with self.assertRaisesRegex(LicenseDocumentError, "another instance"):
            verify_document(document, uuid.uuid4())
        now = timezone.now()
        expired = sign_document(claims(
            instance_id, now - timedelta(seconds=1), issued=now - timedelta(hours=1)
        ))
        with self.assertRaisesRegex(LicenseDocumentError, "expired"):
            verify_document(expired, instance_id)
        payload, signature = document.split(".")
        bad_document = f"{payload}.{'A' if signature[0] != 'A' else 'B'}{signature[1:]}"
        with self.assertRaises(LicenseDocumentError):
            verify_document(bad_document, instance_id)

    def test_raw_key_is_encrypted_and_never_rendered(self):
        key = "jgl_super-secret-license"
        instance = InstanceLicense.current()
        with patch.dict(os.environ, {"LICENSE_ENCRYPTION_KEYS": Fernet.generate_key().decode()}):
            with patch("commercial.service._fetch_document", return_value=sign_document(claims(instance.instance_id))):
                install(key)
        stored = InstanceLicense.current()
        self.assertNotIn(key, stored.encrypted_key)
        self.assertNotIn(key, repr(stored))

        user = get_user_model().objects.create_user("owner")
        self.client.force_login(user)
        response = self.client.get(reverse("license"))
        self.assertNotContains(response, key)

        stored.signed_document = sign_document(claims(stored.instance_id))
        stored.status = "valid"
        stored.save(update_fields=["signed_document", "status"])
        self.assertEqual(self.client.get(reverse("organization_controls")).status_code, 200)
        self.client.post(reverse("license_remove"))
        stored.refresh_from_db()
        self.assertEqual((stored.encrypted_key, stored.signed_document, stored.status), ("", "", ""))
        self.assertEqual(self.client.get(reverse("organization_controls")).status_code, 403)

    def test_outage_uses_existing_document_only_until_fixed_grace_deadline(self):
        instance = InstanceLicense.current()
        key = Fernet.generate_key().decode()
        with patch.dict(os.environ, {"LICENSE_ENCRYPTION_KEYS": key}):
            from commercial.crypto import encrypt_license_key

            instance.encrypted_key = encrypt_license_key("jgl_key")
            instance.signed_document = sign_document(
                claims(
                    instance.instance_id,
                    timezone.now() - timedelta(minutes=1),
                    issued=timezone.now() - timedelta(hours=1),
                )
            )
            instance.grace_deadline = timezone.now() + timedelta(hours=1)
            original_deadline = instance.grace_deadline
            instance.save()
            with override_settings(LICENSE_VALIDATION_URL="https://licenses.example/validate"):
                failures = (
                    urllib.error.URLError("offline"),
                    urllib.error.HTTPError("https://licenses.example", 503, "unavailable", {}, None),
                )
                for failure in failures:
                    with self.subTest(failure=type(failure).__name__):
                        with patch("commercial.service.urllib.request.urlopen", side_effect=failure):
                            with self.assertRaises(LicenseServerError):
                                refresh(instance)
            instance.refresh_from_db()
            self.assertEqual(instance.status, "grace")
            self.assertEqual(instance.grace_deadline, original_deadline)
            self.assertIsNotNone(usable_claims(instance))
            instance.grace_deadline = timezone.now() - timedelta(seconds=1)
            instance.save(update_fields=["grace_deadline"])
            self.assertIsNone(usable_claims(instance))

    def test_malformed_success_response_fails_closed_without_grace(self):
        instance = InstanceLicense.current()
        with patch.dict(os.environ, {"LICENSE_ENCRYPTION_KEYS": Fernet.generate_key().decode()}):
            from commercial.crypto import encrypt_license_key

            instance.encrypted_key = encrypt_license_key("jgl_key")
            instance.signed_document = sign_document(claims(instance.instance_id))
            instance.status = "valid"
            instance.grace_deadline = timezone.now() + timedelta(days=1)
            instance.save()
            with override_settings(LICENSE_VALIDATION_URL="https://licenses.example/validate"):
                with patch("commercial.service.urllib.request.urlopen", return_value=Response(b"not-json")):
                    with self.assertRaises(LicenseServerError):
                        refresh(instance)
        instance.refresh_from_db()
        self.assertEqual((instance.status, instance.signed_document), ("invalid", ""))

    def test_rejected_response_fails_closed_without_grace(self):
        instance = InstanceLicense.current()
        with patch.dict(os.environ, {"LICENSE_ENCRYPTION_KEYS": Fernet.generate_key().decode()}):
            from commercial.crypto import encrypt_license_key

            instance.encrypted_key = encrypt_license_key("jgl_key")
            instance.signed_document = sign_document(claims(instance.instance_id))
            instance.status = "valid"
            instance.grace_deadline = timezone.now() + timedelta(days=1)
            instance.save()
            rejected = urllib.error.HTTPError("https://licenses.example", 403, "rejected", {}, None)
            with override_settings(LICENSE_VALIDATION_URL="https://licenses.example/validate"):
                with patch("commercial.service.urllib.request.urlopen", side_effect=rejected):
                    with self.assertRaises(LicenseServerError):
                        refresh(instance)
        instance.refresh_from_db()
        self.assertEqual((instance.status, instance.signed_document), ("invalid", ""))

    def test_failed_replacement_preserves_working_license(self):
        instance = InstanceLicense.current()
        encryption_key = Fernet.generate_key().decode()
        old_document = sign_document(claims(instance.instance_id))
        with patch.dict(os.environ, {"LICENSE_ENCRYPTION_KEYS": encryption_key}):
            with patch("commercial.service._fetch_document", return_value=old_document):
                install("jgl_old")
            instance.refresh_from_db()
            old_state = (
                instance.encrypted_key,
                instance.signed_document,
                instance.status,
                instance.validated_at,
                instance.grace_deadline,
            )
            wrong_document = sign_document(claims(uuid.uuid4()))
            with patch("commercial.service._fetch_document", return_value=wrong_document):
                with self.assertRaises(LicenseServerError):
                    install("jgl_replacement")
        instance.refresh_from_db()
        self.assertEqual(
            (
                instance.encrypted_key,
                instance.signed_document,
                instance.status,
                instance.validated_at,
                instance.grace_deadline,
            ),
            old_state,
        )

    def test_document_time_boundaries(self):
        instance_id = uuid.uuid4()
        now = timezone.now().replace(microsecond=0)
        skew = 300
        maximum = 86400

        for field in ("iat", "exp"):
            invalid = claims(instance_id, issued=now)
            invalid[field] = True
            with self.subTest(field=field), self.assertRaises(LicenseDocumentError):
                verify_document(sign_document(invalid), instance_id, now=now)

        future = claims(instance_id, issued=now)
        future["iat"] = int((now + timedelta(seconds=skew + 1)).timestamp())
        future["exp"] = future["iat"] + 1
        with self.assertRaisesRegex(LicenseDocumentError, "issue time"):
            verify_document(sign_document(future), instance_id, now=now)

        too_old = claims(instance_id, issued=now)
        too_old["iat"] = int((now - timedelta(seconds=maximum + skew + 1)).timestamp())
        too_old["exp"] = int((now + timedelta(seconds=1)).timestamp())
        with self.assertRaisesRegex(LicenseDocumentError, "too old"):
            verify_document(sign_document(too_old), instance_id, now=now)

        reversed_times = claims(instance_id, issued=now)
        reversed_times["exp"] = reversed_times["iat"]
        with self.assertRaisesRegex(LicenseDocumentError, "validity period"):
            verify_document(sign_document(reversed_times), instance_id, now=now)

        too_long = claims(instance_id, issued=now)
        too_long["exp"] = too_long["iat"] + maximum + skew + 1
        with self.assertRaisesRegex(LicenseDocumentError, "validity period"):
            verify_document(sign_document(too_long), instance_id, now=now)

        boundary = claims(instance_id, issued=now)
        boundary["iat"] = int((now + timedelta(seconds=skew)).timestamp())
        boundary["exp"] = boundary["iat"] + maximum + skew
        self.assertEqual(verify_document(sign_document(boundary), instance_id, now=now)["iat"], boundary["iat"])


@SIGNING
@override_settings(DEPLOYMENT_MODE="cloud")
class CloudLicenseServerTests(TestCase):
    def setUp(self):
        self.key = "jgl_high-entropy-test-key"
        self.license = IssuedLicense.objects.create(
            key_hash=opaque_hash(self.key),
            organization="Example AG",
            entitlements=["organization_controls"],
            expires_at=timezone.now() + timedelta(days=30),
        )

    def validate(self, instance_id, key=None):
        return self.client.post(
            reverse("validate_license"),
            json.dumps({"license_key": key or self.key, "instance_id": str(instance_id)}),
            content_type="application/json",
        )

    def test_validation_binds_first_instance_and_rejects_other_instance(self):
        instance_id = uuid.uuid4()
        response = self.validate(instance_id)
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, self.key)
        document = response.json()["signed_document"]
        self.assertEqual(verify_document(document, instance_id)["organization"], "Example AG")
        self.license.refresh_from_db()
        self.assertEqual(self.license.bound_instance_id, instance_id)
        self.assertEqual(self.validate(uuid.uuid4()).status_code, 403)
        self.assertEqual(self.validate(instance_id, "wrong-key").status_code, 403)

    def test_revoked_and_expired_licenses_are_rejected(self):
        self.license.revoked_at = timezone.now()
        self.license.save()
        self.assertEqual(self.validate(uuid.uuid4()).status_code, 403)
        self.license.revoked_at = None
        self.license.expires_at = timezone.now() - timedelta(seconds=1)
        self.license.save()
        self.assertEqual(self.validate(uuid.uuid4()).status_code, 403)

    def test_invalid_record_or_signing_failure_does_not_bind_instance(self):
        self.license.organization = ""
        self.license.save()
        self.assertEqual(self.validate(uuid.uuid4()).status_code, 403)
        self.license.refresh_from_db()
        self.assertIsNone(self.license.bound_instance_id)

        self.license.organization = "Example AG"
        self.license.save()
        with override_settings(LICENSE_SIGNING_PRIVATE_KEY="invalid"):
            self.assertEqual(self.validate(uuid.uuid4()).status_code, 503)
        self.license.refresh_from_db()
        self.assertIsNone(self.license.bound_instance_id)

    def test_issue_and_revoke_commands_use_safe_identifier(self):
        output = io.StringIO()
        call_command("issue_license", "Command Org", "--entitlement", "organization_controls", stdout=output)
        issued = IssuedLicense.objects.exclude(pk=self.license.pk).get()
        self.assertNotIn(issued.key_hash, output.getvalue())
        call_command("revoke_license", str(issued.pk), stdout=io.StringIO())
        issued.refresh_from_db()
        self.assertIsNotNone(issued.revoked_at)
