import base64
import hashlib
import json
from datetime import timedelta
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlsplit

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from gateway.models import OAuthAccessToken, OAuthAuthorizationCode, OAuthClient, OAuthRefreshToken


@override_settings(PUBLIC_BASE_URL="https://juraguard.example")
class OAuthServerTests(TestCase):
    resource = "https://juraguard.example/mcp/"
    redirect_uri = "https://client.example/callback"
    verifier = "v" * 64
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()

    def setUp(self):
        self.user = get_user_model().objects.create_user("owner", password="safe-test-password")
        self.client.force_login(self.user)
        self.oauth_client = OAuthClient.objects.create(
            client_id="public-client", client_name="Test client", redirect_uris=[self.redirect_uri],
        )

    def authorize(self, **changes):
        data = {
            "response_type": "code",
            "client_id": self.oauth_client.client_id,
            "redirect_uri": self.redirect_uri,
            "resource": self.resource,
            "state": "opaque-state",
            "code_challenge": self.challenge,
            "code_challenge_method": "S256",
            "decision": "approve",
            **changes,
        }
        response = self.client.post("/oauth/authorize/", data)
        code = parse_qs(urlsplit(response.headers["Location"]).query)["code"][0] if response.status_code == 302 else None
        return response, code

    def exchange(self, code, **changes):
        return self.client.post("/oauth/token/", {
            "grant_type": "authorization_code",
            "code": code,
            "client_id": self.oauth_client.client_id,
            "redirect_uri": self.redirect_uri,
            "resource": self.resource,
            "code_verifier": self.verifier,
            **changes,
        })

    def test_metadata_dcr_and_bearer_challenge(self):
        protected = self.client.get("/.well-known/oauth-protected-resource/mcp/").json()
        self.assertEqual(protected["resource"], self.resource)
        self.assertEqual(protected["authorization_servers"], ["https://juraguard.example"])
        server = self.client.get("/.well-known/oauth-authorization-server").json()
        self.assertTrue(server["client_id_metadata_document_supported"])
        self.assertEqual(server["scopes_supported"], ["mcp:read", "mcp:write"])
        self.assertEqual(protected["scopes_supported"], ["mcp:read", "mcp:write"])
        rejected = self.client.post("/oauth/register/", data=json.dumps({
            "redirect_uris": ["http://evil.example/callback"],
        }), content_type="application/json")
        self.assertEqual(rejected.status_code, 400)
        registered = self.client.post("/oauth/register/", data=json.dumps({
            "client_name": "OpenCode", "redirect_uris": ["http://localhost:19876/callback"],
            "token_endpoint_auth_method": "none",
        }), content_type="application/json")
        self.assertEqual(registered.status_code, 201)
        duplicate = self.client.post("/oauth/register/", data=json.dumps({
            "client_name": "OpenCode", "redirect_uris": ["http://localhost:19876/callback"],
            "token_endpoint_auth_method": "none",
        }), content_type="application/json")
        self.assertEqual(duplicate.json()["client_id"], registered.json()["client_id"])
        challenge = self.client.post("/mcp/", data="{}", content_type="application/json")
        self.assertIn("resource_metadata=", challenge.headers["WWW-Authenticate"])

    def test_dcr_prunes_expired_clients_uses_short_ttl_and_bounds_body(self):
        OAuthClient.objects.create(
            client_id="expired", client_name="Old", redirect_uris=[self.redirect_uri],
            is_dynamic=True, expires_at=timezone.now() - timedelta(seconds=1),
        )
        payload = {"client_name": "One", "redirect_uris": [self.redirect_uri]}
        self.assertEqual(self.client.post(
            "/oauth/register/", data=json.dumps(payload), content_type="application/json",
        ).status_code, 201)
        payload["client_name"] = "Two"
        self.assertEqual(self.client.post(
            "/oauth/register/", data=json.dumps(payload), content_type="application/json",
        ).status_code, 201)
        expiry = OAuthClient.objects.get(client_name="Two").expires_at
        self.assertLessEqual(expiry, timezone.now() + timedelta(minutes=10))
        oversized = self.client.post(
            "/oauth/register/", data="{}", content_type="application/json", HTTP_CONTENT_LENGTH="65537",
        )
        self.assertEqual(oversized.json()["error"], "invalid_client_metadata")
        self.assertFalse(OAuthClient.objects.filter(client_id="expired").exists())

    @patch("gateway.oauth_server.outbound_request")
    def test_cimd_requires_identity_and_revalidates_each_authorization(self, mocked):
        client_id = "https://client.example/oauth/metadata.json"
        response = MagicMock()
        response.json.return_value = {
            "client_id": client_id,
            "client_name": "Remote client",
            "redirect_uris": [self.redirect_uri],
            "token_endpoint_auth_method": "none",
        }
        mocked.return_value = response
        for _ in range(2):
            result, _ = self.authorize(client_id=client_id)
            self.assertEqual(result.status_code, 302)
        self.assertEqual(mocked.call_count, 2)
        response.json.return_value["client_name"] = ""
        result, _ = self.authorize(client_id=client_id)
        self.assertEqual(result.status_code, 400)
        no_path, _ = self.authorize(client_id="https://client.example")
        self.assertEqual(no_path.status_code, 400)

    def test_approval_displays_redirect_and_localhost_warning(self):
        self.oauth_client.redirect_uris = ["http://localhost:9876/callback"]
        self.oauth_client.save(update_fields=["redirect_uris"])
        response = self.client.get("/oauth/authorize/", {
            "response_type": "code", "client_id": self.oauth_client.client_id,
            "redirect_uri": self.oauth_client.redirect_uris[0], "resource": self.resource,
            "code_challenge": self.challenge, "code_challenge_method": "S256",
        })
        self.assertContains(response, "http://localhost:9876/callback")
        self.assertContains(response, "Localhost warning")
        self.assertContains(response, "mcp:read")
        self.assertContains(response, "explicitly read-only integration tools")

    def test_scope_defaults_to_read_and_is_persisted(self):
        _, code = self.authorize()
        self.assertEqual(OAuthAuthorizationCode.objects.get().scope, "mcp:read")
        issued = self.exchange(code).json()
        self.assertEqual(issued["scope"], "mcp:read")
        self.assertEqual(OAuthAccessToken.objects.get().scope, "mcp:read")
        self.assertEqual(OAuthRefreshToken.objects.get().scope, "mcp:read")

    def test_explicit_scopes_are_normalized_and_persisted(self):
        _, code = self.authorize(scope="mcp:write mcp:read mcp:write")
        self.assertEqual(OAuthAuthorizationCode.objects.get().scope, "mcp:read mcp:write")
        issued = self.exchange(code).json()
        self.assertEqual(issued["scope"], "mcp:read mcp:write")
        self.assertEqual(OAuthRefreshToken.objects.get().scope, "mcp:read mcp:write")

    def test_pkce_code_is_bound_one_use_and_authenticates_mcp(self):
        response, code = self.authorize()
        self.assertIn("state=opaque-state", response.headers["Location"])
        bad = self.exchange(code, code_verifier="x" * 64)
        self.assertEqual(bad.json()["error"], "invalid_grant")
        token = self.exchange(code).json()["access_token"]
        self.assertNotEqual(token, OAuthAccessToken.objects.get().token_hash)
        self.assertEqual(self.exchange(code).json()["error"], "invalid_grant")
        rpc = self.client.post(
            "/mcp/", data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping", "params": {}}),
            content_type="application/json", HTTP_AUTHORIZATION=f"Bearer {token}",
        )
        self.assertEqual(rpc.status_code, 200)

    def test_bad_expired_code_and_wrong_resource_are_rejected(self):
        response, _ = self.authorize(resource="https://wrong.example/mcp/")
        self.assertEqual(response.status_code, 400)
        _, code = self.authorize()
        OAuthAuthorizationCode.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(self.exchange(code).json()["error"], "invalid_grant")
        _, code = self.authorize()
        self.assertEqual(self.exchange(code, resource="https://wrong.example/mcp/").json()["error"], "invalid_grant")

    def test_unsupported_scopes_are_rejected(self):
        for scope in ("", "admin", "mcp:read  mcp:write"):
            with self.subTest(scope=scope):
                response, _ = self.authorize(scope=scope)
                self.assertEqual(response.status_code, 400)
        _, code = self.authorize()
        self.assertEqual(self.exchange(code, scope="admin").json()["error"], "invalid_grant")

    def test_code_and_refresh_exchanges_cannot_elevate_scope(self):
        _, code = self.authorize(scope="mcp:read")
        self.assertEqual(self.exchange(code, scope="mcp:write").json()["error"], "invalid_grant")
        issued = self.exchange(code).json()
        elevated = self.client.post("/oauth/token/", {
            "grant_type": "refresh_token", "refresh_token": issued["refresh_token"],
            "client_id": self.oauth_client.client_id, "resource": self.resource, "scope": "mcp:write",
        })
        self.assertEqual(elevated.json()["error"], "invalid_scope")
        rotated = self.client.post("/oauth/token/", {
            "grant_type": "refresh_token", "refresh_token": issued["refresh_token"],
            "client_id": self.oauth_client.client_id, "resource": self.resource,
        }).json()
        self.assertEqual(rotated["scope"], "mcp:read")
        self.assertEqual(OAuthRefreshToken.objects.get(token_hash=hashlib.sha256(
            rotated["refresh_token"].encode(),
        ).hexdigest()).scope, "mcp:read")

    def test_access_expiry_refresh_rotation_reuse_and_revocation(self):
        _, code = self.authorize()
        issued = self.exchange(code).json()
        access = issued["access_token"]
        refresh = issued["refresh_token"]
        OAuthAccessToken.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        rpc = self.client.post("/mcp/", data="{}", content_type="application/json",
                               HTTP_AUTHORIZATION=f"Bearer {access}")
        self.assertEqual(rpc.status_code, 401)
        rotated = self.client.post("/oauth/token/", {
            "grant_type": "refresh_token", "refresh_token": refresh,
            "client_id": self.oauth_client.client_id, "resource": self.resource,
        }).json()
        self.assertIn("access_token", rotated)
        reused = self.client.post("/oauth/token/", {
            "grant_type": "refresh_token", "refresh_token": refresh,
            "client_id": self.oauth_client.client_id, "resource": self.resource,
        })
        self.assertEqual(reused.json()["error"], "invalid_grant")
        self.assertFalse(OAuthRefreshToken.objects.filter(revoked_at__isnull=True).exists())
        self.client.post("/oauth/revoke/", {"token": rotated["access_token"]})
        self.assertIsNotNone(OAuthAccessToken.objects.get(token_hash=hashlib.sha256(
            rotated["access_token"].encode()).hexdigest()).revoked_at)

    def test_refresh_replay_revokes_only_its_access_family(self):
        _, first_code = self.authorize()
        first = self.exchange(first_code).json()
        _, second_code = self.authorize()
        second = self.exchange(second_code).json()
        self.client.post("/oauth/token/", {
            "grant_type": "refresh_token", "refresh_token": first["refresh_token"],
            "client_id": self.oauth_client.client_id, "resource": self.resource,
        })
        self.client.post("/oauth/token/", {
            "grant_type": "refresh_token", "refresh_token": first["refresh_token"],
            "client_id": self.oauth_client.client_id, "resource": self.resource,
        })
        second_row = OAuthAccessToken.objects.get(token_hash=hashlib.sha256(second["access_token"].encode()).hexdigest())
        self.assertIsNone(second_row.revoked_at)
        self.assertIsNotNone(second_row.family_id)

    def test_machine_endpoints_reject_oversized_forms(self):
        for path in ("/oauth/token/", "/oauth/revoke/"):
            response = self.client.post(path, {}, HTTP_CONTENT_LENGTH="65537")
            self.assertEqual(response.status_code, 400)
            self.assertEqual(response.json()["error"], "invalid_request")
