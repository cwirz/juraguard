import tempfile
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlsplit
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from gateway.models import (
    GatewayToken,
    Integration,
    OAuthAccessToken,
    OAuthClient,
    OAuthRefreshToken,
    Workspace,
)
from gateway.views import _create_first_owner


class WebFlowTests(TestCase):
    def test_users_receive_one_personal_workspace(self):
        user = get_user_model().objects.create_user("owner")

        self.assertEqual(Workspace.objects.get(owner=user), user.workspace)
        self.assertEqual(Workspace.objects.filter(owner=user).count(), 1)

    @override_settings(
        DEPLOYMENT_MODE="self_hosted", OWNER_SETUP_DEADLINE=4_102_444_800, OWNER_SETUP_TOKEN="setup-secret",
    )
    def test_self_hosted_disables_public_account_routes(self):
        self.assertEqual(self.client.get("/accounts/signup/").status_code, 404)
        self.assertEqual(self.client.get("/accounts/google/login/").status_code, 404)
        self.assertEqual(self.client.get("/setup/").status_code, 404)
        self.assertEqual(self.client.get("/setup/?token=wrong").status_code, 404)
        self.assertRedirects(self.client.get("/setup/?token=setup-secret"), "/setup/")
        self.assertEqual(self.client.get("/setup/").status_code, 200)

    @override_settings(
        DEPLOYMENT_MODE="cloud",
        ACCOUNT_EMAIL_VERIFICATION="mandatory",
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    )
    def test_cloud_enables_signup_and_disables_owner_setup(self):
        self.assertContains(self.client.get("/login/"), "Log in")
        self.assertContains(self.client.get("/accounts/signup/"), "Sign up")
        self.assertEqual(self.client.get("/setup/").status_code, 404)
        response = self.client.post("/accounts/signup/", {
            "email": "cloud@juraguard.test",
            "password1": "correct-horse-staple-9472",
            "password2": "correct-horse-staple-9472",
        })
        user = get_user_model().objects.get(email="cloud@juraguard.test")
        self.assertRedirects(response, "/accounts/confirm-email/")
        self.assertTrue(Workspace.objects.filter(owner=user).exists())
        self.assertNotIn("_auth_user_id", self.client.session)

        confirmation = mail.outbox[0]
        self.assertEqual(
            confirmation.subject, "[JuraGuard] Please Confirm Your Email Address"
        )
        self.assertNotIn("example.com", confirmation.body)
        self.assertIn("registered a JuraGuard account", confirmation.body)
        confirmation_path = urlsplit(
            next(line for line in confirmation.body.splitlines() if line.startswith("http"))
        ).path
        self.assertRedirects(self.client.get(confirmation_path), "/dashboard/")
        self.assertEqual(int(self.client.session["_auth_user_id"]), user.pk)
        self.assertTrue(user.emailaddress_set.get().verified)

    @override_settings(
        DEPLOYMENT_MODE="cloud",
        ACCOUNT_EMAIL_VERIFICATION="mandatory",
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    )
    def test_confirmation_without_signup_session_redirects_to_login(self):
        self.client.post("/accounts/signup/", {
            "email": "fresh@juraguard.test",
            "password1": "correct-horse-staple-9472",
            "password2": "correct-horse-staple-9472",
        })
        confirmation_path = urlsplit(
            next(line for line in mail.outbox[0].body.splitlines() if line.startswith("http"))
        ).path

        fresh_client = Client()
        self.assertRedirects(fresh_client.get(confirmation_path), "/accounts/login/")
        self.assertNotIn("_auth_user_id", fresh_client.session)
        user = get_user_model().objects.get(email="fresh@juraguard.test")
        self.assertTrue(user.emailaddress_set.get().verified)

    @override_settings(OWNER_SETUP_DEADLINE=4_102_444_800, OWNER_SETUP_TOKEN="setup-secret")
    def test_first_run_owner_setup_login_and_token_rotation(self):
        self.client.get("/setup/?token=setup-secret")
        response = self.client.post("/setup/", {
            "username": "owner",
            "email": "owner@example.com",
            "password1": "correct-horse-staple-9472",
            "password2": "correct-horse-staple-9472",
        })
        self.assertRedirects(response, "/dashboard/")
        self.assertEqual(get_user_model().objects.count(), 1)
        response = self.client.post("/dashboard/token/rotate/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "mcpg_")
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertEqual(response.headers["Pragma"], "no-cache")
        self.assertContains(response, 'data-replace-url="/dashboard/"')
        self.assertNotIn("shown_gateway_token", self.client.session)
        token = GatewayToken.objects.get()
        self.assertNotContains(response, token.token_hash)
        self.assertNotContains(self.client.get("/dashboard/"), "mcpg_")

    @override_settings(DEPLOYMENT_MODE="self_hosted", OWNER_SETUP_DEADLINE=0)
    def test_owner_setup_closes_after_initial_window(self):
        self.assertEqual(self.client.get("/setup/").status_code, 404)
        self.assertEqual(self.client.post("/setup/", {}).status_code, 404)

    def test_first_owner_helper_prevents_second_owner(self):
        with tempfile.TemporaryDirectory() as directory, override_settings(DATA_DIR=Path(directory)):
            first_form = MagicMock()
            first_form.save.side_effect = lambda: get_user_model().objects.create_user("first")
            second_form = MagicMock()

            self.assertIsNotNone(_create_first_owner(first_form))
            self.assertIsNone(_create_first_owner(second_form))

        second_form.save.assert_not_called()
        self.assertEqual(get_user_model().objects.count(), 1)

    @patch("gateway.views.connection")
    def test_first_owner_uses_postgres_advisory_lock_before_creation(self, mocked_connection):
        events = []
        mocked_connection.vendor = "postgresql"
        cursor = mocked_connection.cursor.return_value.__enter__.return_value
        cursor.execute.side_effect = lambda *args: events.append("lock")
        form = MagicMock()

        def save():
            events.append("save")
            return get_user_model().objects.create_user("owner")

        form.save.side_effect = save

        self.assertIsNotNone(_create_first_owner(form))
        self.assertEqual(events, ["lock", "save"])
        cursor.execute.assert_called_once_with("SELECT pg_advisory_xact_lock(%s)", [8472645110252201])

    def test_dashboard_integrations_are_user_scoped(self):
        owner = get_user_model().objects.create_user("owner", password="password")
        other = get_user_model().objects.create_user("other", password="password")
        Integration.objects.create(
            workspace=other.workspace, name="Hidden", slug="hidden", remote_url="https://example.com/mcp"
        )
        self.client.force_login(owner)
        self.assertNotContains(self.client.get("/dashboard/"), "Hidden")
        self.assertEqual(self.client.get(f"/integrations/{other.workspace.integrations.first().pk}/edit/").status_code, 404)

    def test_direct_integration_mutations_are_tenant_scoped(self):
        owner = get_user_model().objects.create_user("owner", password="password")
        other = get_user_model().objects.create_user("other", password="password")
        integration = Integration.objects.create(
            workspace=other.workspace, name="Hidden", slug="hidden", remote_url="https://example.com/mcp"
        )
        self.client.force_login(owner)
        for suffix in ("toggle", "reconnect", "delete"):
            with self.subTest(suffix=suffix):
                response = self.client.post(f"/integrations/{integration.pk}/{suffix}/")
                self.assertEqual(response.status_code, 404)
                self.assertTrue(Integration.objects.filter(pk=integration.pk, workspace=other.workspace).exists())

    def test_state_changes_require_login_post_and_csrf(self):
        owner = get_user_model().objects.create_user("owner", password="password")
        integration = Integration.objects.create(
            workspace=owner.workspace, name="Remote", slug="remote", remote_url="https://example.com/mcp"
        )
        self.assertEqual(self.client.get(f"/integrations/{integration.pk}/delete/").status_code, 302)
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(owner)
        self.assertEqual(csrf_client.post(f"/integrations/{integration.pk}/delete/").status_code, 403)
        self.assertTrue(Integration.objects.filter(pk=integration.pk).exists())

    def test_owner_oauth_client_list_shows_only_current_grants(self):
        owner = get_user_model().objects.create_user("owner")
        other = get_user_model().objects.create_user("other")
        current = OAuthClient.objects.create(
            client_id="current-client", client_name="Current app", redirect_uris=["https://client.example/callback"],
        )
        hidden = OAuthClient.objects.create(
            client_id="hidden-client", client_name="Hidden app", redirect_uris=["https://hidden.example/callback"],
        )
        now = timezone.now()
        OAuthAccessToken.objects.create(
            user=owner, client=current, token_hash="a" * 64, resource="https://juraguard.example/mcp/",
            scope="mcp:read", expires_at=now + timedelta(hours=1),
        )
        OAuthRefreshToken.objects.create(
            user=owner, client=current, token_hash="b" * 64, resource="https://juraguard.example/mcp/",
            scope="mcp:read mcp:write", expires_at=now + timedelta(days=1),
        )
        OAuthAccessToken.objects.create(
            user=owner, client=hidden, token_hash="c" * 64, resource="https://juraguard.example/mcp/",
            expires_at=now - timedelta(seconds=1),
        )
        OAuthAccessToken.objects.create(
            user=other, client=hidden, token_hash="d" * 64, resource="https://juraguard.example/mcp/",
            expires_at=now + timedelta(hours=1),
        )
        self.client.force_login(owner)

        response = self.client.get("/oauth/clients/")

        self.assertContains(response, "Current app")
        self.assertContains(response, "current-client")
        self.assertContains(response, "mcp:read mcp:write")
        self.assertContains(response, "Access expires")
        self.assertContains(response, "Refresh expires")
        self.assertNotContains(response, "Hidden app")
        self.assertNotContains(response, "a" * 64)

    def test_owner_revocation_is_client_and_user_scoped(self):
        owner = get_user_model().objects.create_user("owner")
        other = get_user_model().objects.create_user("other")
        oauth_client = OAuthClient.objects.create(
            client_id="shared-client", client_name="Shared app", redirect_uris=["https://client.example/callback"],
        )
        other_client = OAuthClient.objects.create(
            client_id="other-client", client_name="Other app", redirect_uris=["https://other.example/callback"],
        )
        expires_at = timezone.now() + timedelta(days=1)
        own_access = OAuthAccessToken.objects.create(
            user=owner, client=oauth_client, token_hash="e" * 64, resource="https://juraguard.example/mcp/",
            expires_at=expires_at,
        )
        own_refresh = OAuthRefreshToken.objects.create(
            user=owner, client=oauth_client, token_hash="f" * 64, resource="https://juraguard.example/mcp/",
            expires_at=expires_at,
        )
        other_access = OAuthAccessToken.objects.create(
            user=other, client=oauth_client, token_hash="1" * 64, resource="https://juraguard.example/mcp/",
            expires_at=expires_at,
        )
        OAuthAccessToken.objects.create(
            user=other, client=other_client, token_hash="2" * 64, resource="https://juraguard.example/mcp/",
            expires_at=expires_at,
        )
        self.client.force_login(owner)

        response = self.client.post(f"/oauth/clients/{oauth_client.pk}/revoke/", follow=True)

        own_access.refresh_from_db()
        own_refresh.refresh_from_db()
        other_access.refresh_from_db()
        self.assertIsNotNone(own_access.revoked_at)
        self.assertIsNotNone(own_refresh.revoked_at)
        self.assertIsNone(other_access.revoked_at)
        self.assertTrue(OAuthClient.objects.filter(pk=oauth_client.pk).exists())
        self.assertContains(response, "Access revoked for Shared app.")
        self.assertEqual(self.client.post(f"/oauth/clients/{other_client.pk}/revoke/").status_code, 404)

    def test_owner_revocation_requires_post_and_csrf(self):
        owner = get_user_model().objects.create_user("owner")
        oauth_client = OAuthClient.objects.create(
            client_id="client", client_name="App", redirect_uris=["https://client.example/callback"],
        )
        OAuthAccessToken.objects.create(
            user=owner, client=oauth_client, token_hash="3" * 64, resource="https://juraguard.example/mcp/",
            expires_at=timezone.now() + timedelta(hours=1),
        )
        self.client.force_login(owner)
        self.assertEqual(self.client.get(f"/oauth/clients/{oauth_client.pk}/revoke/").status_code, 405)
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(owner)
        self.assertEqual(csrf_client.post(f"/oauth/clients/{oauth_client.pk}/revoke/").status_code, 403)

    @override_settings(PUBLIC_BASE_URL="https://review.juraguard.example")
    def test_public_pages_and_health(self):
        landing = self.client.get("/")
        self.assertContains(landing, "Free beta")
        self.assertContains(landing, "50% off")
        self.assertContains(landing, "All code is AGPL-3.0")
        self.assertContains(landing, "supported path during public beta")
        self.assertNotContains(landing, "USD 5 / month")
        docs = self.client.get("/docs/")
        for client_name in ("Claude", "Cursor", "VS Code", "Codex", "OpenCode", "Gemini", "Windsurf"):
            self.assertContains(docs, client_name)
        self.assertContains(docs, "OAuth is preferred for every AI client")
        self.assertContains(docs, "https://review.juraguard.example/mcp/")
        self.assertContains(
            docs,
            "opencode mcp add juraguard --url https://review.juraguard.example/mcp/",
        )
        self.assertNotContains(docs, "juraguard.example.com")
        self.assertEqual(self.client.get("/health/").json(), {"status": "ok"})
        self.assertEqual(self.client.get("/health/live/").json(), {"status": "ok"})
        self.assertEqual(self.client.get("/health/ready/").json(), {"status": "ok"})

    @override_settings(SECURE_SSL_REDIRECT=True)
    def test_health_probes_bypass_https_redirect(self):
        for path in ("/health/", "/health/live/", "/health/ready/"):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 200)
