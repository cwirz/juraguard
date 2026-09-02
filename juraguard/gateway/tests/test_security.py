import json
from datetime import timedelta
from importlib import import_module
from unittest.mock import patch

from cryptography.fernet import Fernet, InvalidToken
from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.utils import timezone

from gateway.dispatch import public_integration
from gateway.forms import IntegrationForm
from gateway.models import Integration, SetupLink
from gateway.remote import RemoteError
from gateway.security import clean_secret_headers, decrypt_headers, encrypt_headers, validate_remote_url


class SecretTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("owner", password="safe-test-password")
        self.integration = Integration.objects.create(
            workspace=self.user.workspace, name="Remote", slug="remote", remote_url="https://example.com/mcp"
        )

    def test_headers_are_encrypted_and_never_exposed(self):
        encrypted = encrypt_headers({"Authorization": "Bearer private-value"})
        self.assertNotIn("private-value", encrypted)
        self.assertEqual(decrypt_headers(encrypted), {"Authorization": "Bearer private-value"})
        self.integration.encrypted_headers = encrypted
        self.integration.save()
        self.client.force_login(self.user)
        self.assertNotContains(self.client.get("/dashboard/"), "private-value")

    @patch("gateway.views.list_tools", return_value=[{"name": "hello", "description": "Say hello"}])
    def test_setup_link_is_hashed_one_use_and_expires(self, mocked_list):
        token = SetupLink.issue(self.integration)
        link = SetupLink.objects.get(integration=self.integration)
        self.assertNotEqual(link.token_hash, token)
        response = self.client.post(
            f"/connect/{token}/",
            {"headers": json.dumps({"Authorization": "Bearer private-value"})},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "is ready")
        self.assertEqual(self.client.get(f"/connect/{token}/").status_code, 404)
        self.integration.refresh_from_db()
        self.assertNotIn("private-value", self.integration.encrypted_headers)
        self.assertEqual(self.integration.get_headers()["Authorization"], "Bearer private-value")
        mocked_list.assert_called_once()

    @patch("gateway.gitlab_provider.validate_pat")
    def test_gitlab_setup_encrypts_pat_without_using_generic_headers(self, validate_pat):
        self.integration.provider_type = Integration.GITLAB
        self.integration.base_url = "https://gitlab.com"
        self.integration.remote_url = ""
        self.integration.save()
        token = SetupLink.issue(self.integration)

        response = self.client.post(f"/connect/{token}/", {"pat": "private-gitlab-pat"})

        self.assertContains(response, "is ready")
        self.integration.refresh_from_db()
        self.assertNotIn("private-gitlab-pat", self.integration.encrypted_credentials)
        self.assertEqual(self.integration.get_credentials()["pat"], "private-gitlab-pat")
        self.assertEqual(self.integration.encrypted_headers, "")
        validate_pat.assert_called_once_with("https://gitlab.com", "private-gitlab-pat")

    def test_secret_headers_allow_authorization_but_reject_protocol_headers(self):
        self.assertEqual(
            clean_secret_headers({"Authorization": "Bearer upstream-only"}),
            {"Authorization": "Bearer upstream-only"},
        )
        blocked = (
            "host",
            "Content-Length",
            "TRANSFER-ENCODING",
            "Connection",
            "Keep-Alive",
            "Accept",
            "accept-encoding",
            "Content-Type",
            "MCP-Protocol-Version",
            "mcp-session-id",
        )
        for name in blocked:
            with self.subTest(name=name), self.assertRaises(ValidationError):
                clean_secret_headers({name: "unsafe"})

    @patch("gateway.views.list_tools", return_value=[])
    def test_setup_submission_cannot_change_tenant_and_replay_fails(self, _mocked_list):
        other = get_user_model().objects.create_user("other", password="safe-test-password")
        other_integration = Integration.objects.create(
            workspace=other.workspace, name="Other", slug="other", remote_url="https://example.org/mcp"
        )
        token = SetupLink.issue(self.integration)
        response = self.client.post(
            f"/connect/{token}/",
            {"headers": '{"Authorization":"Bearer tenant-a"}', "integration": other_integration.pk},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.post(f"/connect/{token}/", {"headers": "{}"}).status_code, 404)
        self.integration.refresh_from_db()
        other_integration.refresh_from_db()
        self.assertEqual(self.integration.get_headers(), {"Authorization": "Bearer tenant-a"})
        self.assertEqual(other_integration.encrypted_headers, "")

    @patch("gateway.views.list_tools", side_effect=RemoteError("provider rejected credentials"))
    def test_failed_setup_is_consumed_without_persisting_secret(self, _mocked_list):
        token = SetupLink.issue(self.integration)
        response = self.client.post(f"/connect/{token}/", {"headers": '{"Authorization":"Bearer rejected"}'})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Connection failed")
        self.assertEqual(self.client.get(f"/connect/{token}/").status_code, 404)
        self.integration.refresh_from_db()
        self.assertEqual(self.integration.encrypted_headers, "")

    def test_expired_and_superseded_setup_links_are_rejected(self):
        expired_token = SetupLink.issue(self.integration)
        SetupLink.objects.filter(integration=self.integration).update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(self.client.get(f"/connect/{expired_token}/").status_code, 404)
        first_token = SetupLink.issue(self.integration)
        second_token = SetupLink.issue(self.integration)
        self.assertEqual(self.client.get(f"/connect/{first_token}/").status_code, 404)
        self.assertEqual(self.client.get(f"/connect/{second_token}/").status_code, 200)

    def test_credential_encryption_supports_rotation_and_fails_closed(self):
        old_key = Fernet.generate_key()
        new_key = Fernet.generate_key()
        with override_settings(CREDENTIAL_ENCRYPTION_KEYS=[old_key.decode()]):
            encrypted_with_old = encrypt_headers({"Authorization": "Bearer rotated"})
        with override_settings(CREDENTIAL_ENCRYPTION_KEYS=[new_key.decode(), old_key.decode()]):
            self.assertEqual(decrypt_headers(encrypted_with_old), {"Authorization": "Bearer rotated"})
            encrypted_with_new = encrypt_headers({"Authorization": "Bearer new"})
        with self.assertRaises(InvalidToken):
            Fernet(old_key).decrypt(encrypted_with_new.encode())

        self.integration.encrypted_headers = encrypted_with_old
        with override_settings(CREDENTIAL_ENCRYPTION_KEYS=[new_key.decode()]):
            self.assertFalse(self.integration.connected)
            with self.assertRaisesMessage(ValueError, "Stored credentials cannot be decrypted."):
                self.integration.get_headers()


class URLValidationTests(TestCase):
    def test_query_strings_fail_validation_and_cannot_leak(self):
        query_url = "https://example.com/mcp?token=secret"
        with self.assertRaises(ValidationError):
            validate_remote_url(query_url, resolve=False)

        user = get_user_model().objects.create_user("query-owner", password="safe-test-password")
        integration = Integration(
            workspace=user.workspace, name="Remote", slug="remote", remote_url=query_url
        )
        with self.assertRaises(ValidationError):
            integration.full_clean()
        integration.save()
        self.assertEqual(public_integration(integration)["remote_url"], "")

        form = IntegrationForm(data={
            "provider_type": Integration.GENERIC_CUSTOM,
            "name": "Remote",
            "slug": "query-remote",
            "remote_url": query_url,
            "base_url": "",
            "active": "on",
        })
        self.assertFalse(form.is_valid())
        self.assertIn("remote_url", form.errors)

    def test_normal_remote_url_passes_model_validation(self):
        user = get_user_model().objects.create_user("normal-owner", password="safe-test-password")
        Integration(
            workspace=user.workspace,
            name="Remote",
            slug="remote",
            remote_url="https://example.com/mcp",
        ).full_clean()

    def test_query_migration_requires_reconnection(self):
        user = get_user_model().objects.create_user("migrated-owner", password="safe-test-password")
        integration = Integration.objects.create(
            workspace=user.workspace,
            name="Migrated",
            slug="migrated",
            remote_url="https://example.com/mcp?token=secret",
            tool_catalog=[{"name": "stale"}],
            catalog_updated_at=timezone.now(),
        )
        integration.set_headers({"Authorization": "Bearer stale"})
        integration.set_credentials({"token": "stale"})
        integration.set_oauth_state({"access_token": "stale"})
        integration.save()

        migration = import_module("gateway.migrations.0012_reject_remote_url_queries")
        migration.remove_existing_queries(apps, None)

        integration.refresh_from_db()
        self.assertEqual(integration.remote_url, "https://example.com/mcp")
        self.assertFalse(integration.active)
        self.assertEqual(integration.encrypted_headers, "")
        self.assertEqual(integration.encrypted_credentials, "")
        self.assertEqual(integration.encrypted_oauth_state, "")
        self.assertEqual(integration.tool_catalog, [])
        self.assertIsNone(integration.catalog_updated_at)

    @override_settings(ALLOW_PRIVATE_NETWORKS=False)
    @patch("gateway.security.socket.getaddrinfo", return_value=[(2, 1, 6, "", ("127.0.0.1", 443))])
    def test_blocks_private_dns_results(self, _resolver):
        with self.assertRaises(ValidationError):
            validate_remote_url("https://internal.example/mcp")

    @override_settings(ALLOW_PRIVATE_NETWORKS=False)
    def test_rejects_http_userinfo_and_fragments(self):
        for url in ("http://example.com/mcp", "https://user:pass@example.com/mcp", "https://example.com/mcp#secret"):
            with self.subTest(url=url), self.assertRaises(ValidationError):
                validate_remote_url(url, resolve=False)

    @override_settings(ALLOW_PRIVATE_NETWORKS=True)
    @patch("gateway.security.socket.getaddrinfo", return_value=[(2, 1, 6, "", ("10.0.0.2", 80))])
    def test_private_opt_in_allows_http(self, _resolver):
        self.assertEqual(validate_remote_url("http://10.0.0.2/mcp"), "http://10.0.0.2/mcp")

    @override_settings(DEPLOYMENT_MODE="cloud", ALLOW_PRIVATE_NETWORKS=True)
    @patch("gateway.security.socket.getaddrinfo", return_value=[(2, 1, 6, "", ("10.0.0.2", 443))])
    def test_cloud_cannot_enable_private_targets(self, _resolver):
        with self.assertRaises(ValidationError):
            validate_remote_url("https://internal.example/mcp")

    @override_settings(ALLOW_PRIVATE_NETWORKS=False)
    @patch("gateway.security.socket.getaddrinfo", return_value=[(2, 1, 6, "", ("10.0.0.2", 443))])
    def test_integration_form_enforces_ssrf_rules(self, _resolver):
        form = IntegrationForm(data={
            "provider_type": Integration.GENERIC_CUSTOM,
            "name": "Internal",
            "slug": "internal",
            "description": "",
            "remote_url": "https://internal.example/mcp",
            "base_url": "",
            "write_enabled": "on",
            "active": "on",
        })
        self.assertFalse(form.is_valid())
        self.assertIn("remote_url", form.errors)

    @override_settings(ALLOW_PRIVATE_NETWORKS=True)
    @patch("gateway.security.socket.getaddrinfo", return_value=[(2, 1, 6, "", ("10.0.0.2", 80))])
    def test_self_host_opt_in_form_retains_write_access(self, _resolver):
        form = IntegrationForm(data={
            "provider_type": Integration.GENERIC_CUSTOM,
            "name": "Internal",
            "slug": "internal",
            "description": "",
            "remote_url": "http://10.0.0.2/mcp",
            "base_url": "https://ignored.example",
            "write_enabled": "on",
            "active": "on",
        })
        self.assertTrue(form.is_valid(), form.errors)
        integration = form.save(commit=False)
        self.assertTrue(integration.write_enabled)
        self.assertEqual(integration.remote_url, "http://10.0.0.2/mcp")
        self.assertEqual(integration.base_url, "")
