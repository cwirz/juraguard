from datetime import timedelta
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlsplit

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from gateway.models import Integration, SetupLink
from gateway.upstream_oauth import UpstreamOAuthError, authorization_headers, discover


def response(payload=None, headers=None, status=200):
    value = MagicMock(status=status, headers=headers or {})
    value.json.return_value = payload or {}
    return value


@override_settings(PUBLIC_BASE_URL="https://juraguard.example")
class UpstreamOAuthTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("owner", password="safe-test-password")
        self.client.force_login(self.user)
        self.integration = Integration.objects.create(
            workspace=self.user.workspace,
            name="OAuth MCP",
            slug="oauth-mcp",
            provider_type=Integration.GENERIC_OAUTH,
            remote_url="https://remote.example/mcp",
        )

    def discovery_responses(self, *, issuer="https://auth.example", response_issuer=False,
                            authorization_endpoint="https://auth.example/authorize"):
        return [
            response(headers={"www-authenticate":
                              'Bearer resource_metadata="https://remote.example/.well-known/resource"'}, status=401),
            response({"resource": self.integration.remote_url, "authorization_servers": ["https://auth.example"]}),
            response({
                "issuer": issuer,
                "authorization_endpoint": authorization_endpoint,
                "token_endpoint": "https://auth.example/token",
                "code_challenge_methods_supported": ["S256"],
                "client_id_metadata_document_supported": True,
                "authorization_response_iss_parameter_supported": response_issuer,
            }),
        ]

    @patch("gateway.upstream_oauth.request")
    def test_discovery_rejects_issuer_mismatch(self, mocked):
        mocked.side_effect = self.discovery_responses(issuer="https://evil.example")
        with self.assertRaisesMessage(UpstreamOAuthError, "issuer does not match"):
            discover(self.integration)

    @patch("gateway.upstream_oauth.request")
    def test_discovery_falls_back_to_oidc_metadata(self, mocked):
        responses = self.discovery_responses()
        responses.insert(2, response(status=404))
        mocked.side_effect = responses
        self.assertEqual(discover(self.integration)["issuer"], "https://auth.example")
        self.assertEqual(mocked.call_args.args[0], "https://auth.example/.well-known/openid-configuration")

    @patch("gateway.upstream_oauth.request")
    def test_discovery_tries_all_path_issuer_locations_in_order(self, mocked):
        responses = self.discovery_responses(issuer="https://auth.example/tenant")
        responses[1] = response({
            "resource": self.integration.remote_url,
            "authorization_servers": ["https://auth.example/tenant"],
        })
        responses[2:3] = [response(status=404), response(status=404), responses[2]]
        mocked.side_effect = responses
        self.assertEqual(discover(self.integration)["issuer"], "https://auth.example/tenant")
        self.assertEqual([call.args[0] for call in mocked.call_args_list[-3:]], [
            "https://auth.example/.well-known/oauth-authorization-server/tenant",
            "https://auth.example/.well-known/openid-configuration/tenant",
            "https://auth.example/tenant/.well-known/openid-configuration",
        ])

    @patch("gateway.upstream_oauth.request")
    def test_authorization_merges_existing_endpoint_query(self, mocked):
        mocked.side_effect = self.discovery_responses(
            authorization_endpoint="https://auth.example/authorize?tenant=one",
        )
        started = self.client.post(f"/integrations/{self.integration.pk}/reconnect/")
        query = parse_qs(urlsplit(started.headers["Location"]).query)
        self.assertEqual(query["tenant"], ["one"])
        self.assertEqual(query["response_type"], ["code"])

    @patch("gateway.upstream_oauth.request")
    def test_private_setup_link_starts_oauth_once(self, mocked):
        mocked.side_effect = self.discovery_responses()
        token = SetupLink.issue(self.integration)

        started = self.client.get(f"/connect/{token}/")

        self.assertEqual(urlsplit(started.headers["Location"]).netloc, "auth.example")
        self.assertFalse(SetupLink.objects.get(integration=self.integration).usable)
        self.assertEqual(self.client.get(f"/connect/{token}/").status_code, 404)

    @patch("gateway.views.start_upstream_oauth", side_effect=UpstreamOAuthError("Discovery failed."))
    def test_private_setup_link_failure_is_consumed_and_recoverable(self, mocked):
        token = SetupLink.issue(self.integration)

        failed = self.client.get(f"/connect/{token}/")

        self.assertContains(failed, "This one-use link was consumed", status_code=400)
        self.assertContains(failed, "Return to dashboard", status_code=400)
        self.assertFalse(SetupLink.objects.get(integration=self.integration).usable)
        self.assertEqual(self.client.get(f"/connect/{token}/").status_code, 404)
        mocked.assert_called_once()

    @patch("gateway.upstream_oauth.request")
    def test_callback_requires_and_validates_authorization_response_issuer(self, mocked):
        mocked.side_effect = self.discovery_responses(response_issuer=True)
        started = self.client.post(f"/integrations/{self.integration.pk}/reconnect/")
        state = parse_qs(urlsplit(started.headers["Location"]).query)["state"][0]
        missing = self.client.get(
            f"/integrations/{self.integration.pk}/oauth/callback/", {"code": "code", "state": state},
        )
        self.assertContains(missing, "issuer is missing", status_code=400)

        mocked.side_effect = self.discovery_responses()
        started = self.client.post(f"/integrations/{self.integration.pk}/reconnect/")
        state = parse_qs(urlsplit(started.headers["Location"]).query)["state"][0]
        wrong = self.client.get(f"/integrations/{self.integration.pk}/oauth/callback/", {
            "code": "code", "state": state, "iss": "https://evil.example",
        })
        self.assertContains(wrong, "issuer does not match", status_code=400)

    @patch("gateway.views.list_tools", return_value=[{"name": "hello"}])
    @patch("gateway.upstream_oauth.request")
    def test_connect_callback_uses_pkce_resource_and_encrypts_tokens(self, mocked, _list_tools):
        mocked.side_effect = self.discovery_responses() + [response({
            "token_type": "Bearer", "access_token": "upstream-access", "refresh_token": "upstream-refresh",
            "expires_in": 3600,
        })]
        started = self.client.post(f"/integrations/{self.integration.pk}/reconnect/")
        self.assertEqual(started.status_code, 302)
        query = parse_qs(urlsplit(started.headers["Location"]).query)
        self.assertEqual(query["resource"], [self.integration.remote_url])
        self.assertEqual(query["code_challenge_method"], ["S256"])
        completed = self.client.get(
            f"/integrations/{self.integration.pk}/oauth/callback/",
            {"code": "remote-code", "state": query["state"][0]},
        )
        self.assertRedirects(completed, "/dashboard/")
        token_form = mocked.call_args.kwargs["form"]
        self.assertEqual(token_form["resource"], self.integration.remote_url)
        self.assertIn("code_verifier", token_form)
        self.integration.refresh_from_db()
        self.assertNotIn("upstream-access", self.integration.encrypted_oauth_state)
        self.assertEqual(self.integration.get_oauth_state()["refresh_token"], "upstream-refresh")

    @patch("gateway.upstream_oauth.request")
    def test_expired_access_token_refreshes_and_rotates(self, mocked):
        self.integration.set_oauth_state({
            "access_token": "old-access",
            "refresh_token": "old-refresh",
            "expires_at": (timezone.now() - timedelta(seconds=1)).isoformat(),
            "client_id": "public-client",
            "token_endpoint": "https://auth.example/token",
            "issuer": "https://auth.example",
            "resource": self.integration.remote_url,
        })
        self.integration.save()
        mocked.return_value = response({
            "token_type": "Bearer", "access_token": "new-access", "refresh_token": "new-refresh",
            "expires_in": 3600,
        })
        self.assertEqual(authorization_headers(self.integration), {"Authorization": "Bearer new-access"})
        self.assertEqual(mocked.call_args.kwargs["form"]["resource"], self.integration.remote_url)
        self.integration.refresh_from_db()
        self.assertEqual(self.integration.get_oauth_state()["refresh_token"], "new-refresh")
