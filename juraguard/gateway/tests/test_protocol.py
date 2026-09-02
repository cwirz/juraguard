import json
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from gateway.models import GatewayToken, Integration, OAuthAccessToken, OAuthClient
from gateway.protocol import MAX_REQUEST_BYTES
from gateway.security import opaque_hash


class ProtocolTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("owner", password="safe-test-password")
        self.token = GatewayToken.issue(self.user)

    def rpc(self, method, params=None, *, token=None, origin=None):
        headers = {"HTTP_AUTHORIZATION": f"Bearer {token or self.token}"}
        if origin:
            headers["HTTP_ORIGIN"] = origin
        return self.client.post(
            "/mcp/",
            data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}),
            content_type="application/json",
            **headers,
        )

    def tool_result(self, name, arguments=None, *, token=None):
        response = self.rpc("tools/call", {"name": name, "arguments": arguments or {}}, token=token)
        text = response.json()["result"]["content"][0]["text"]
        try:
            result = json.loads(text)
        except json.JSONDecodeError:
            result = text
        return response, result

    def oauth_token(self, scope):
        raw = f"oauth-{scope}"
        client = OAuthClient.objects.create(
            client_id=f"client-{scope}", client_name=scope, redirect_uris=["https://client.example/callback"],
        )
        OAuthAccessToken.objects.create(
            user=self.user,
            client=client,
            token_hash=opaque_hash(raw),
            resource="http://testserver/mcp/",
            scope=scope,
            expires_at=timezone.now() + timedelta(hours=1),
        )
        return raw

    def test_auth_initialize_and_request_validation(self):
        self.assertEqual(self.client.post("/mcp/", data="{}", content_type="application/json").status_code, 401)
        response = self.rpc("initialize", {"protocolVersion": "2025-06-18"})
        self.assertEqual(response.json()["result"]["protocolVersion"], "2025-06-18")
        self.assertEqual(response.headers["MCP-Protocol-Version"], "2025-06-18")
        invalid = self.client.post(
            "/mcp/", data="[]", content_type="application/json", HTTP_AUTHORIZATION=f"Bearer {self.token}"
        )
        self.assertEqual(invalid.json()["error"]["code"], -32600)

    def test_content_type_and_actual_body_size_are_enforced(self):
        authorization = f"Bearer {self.token}"
        unsupported = self.client.post("/mcp/", data="{}", content_type="text/plain", HTTP_AUTHORIZATION=authorization)
        self.assertEqual(unsupported.status_code, 415)

        oversized = b"x" * (MAX_REQUEST_BYTES + 1)
        for declared_length in ("", "1"):
            with self.subTest(declared_length=declared_length):
                response = self.client.generic(
                    "POST",
                    "/mcp/",
                    oversized,
                    content_type="application/json",
                    HTTP_AUTHORIZATION=authorization,
                    CONTENT_LENGTH=declared_length,
                )
                self.assertEqual(response.status_code, 413)

    def test_origin_must_match_host(self):
        self.assertEqual(self.rpc("ping", origin="https://evil.example").status_code, 403)
        self.assertEqual(self.rpc("ping", origin="http://testserver").status_code, 200)

    def test_tools_list_is_compact(self):
        integration = Integration.objects.create(
            workspace=self.user.workspace,
            name="Huge MCP",
            slug="huge",
            remote_url="https://example.com/mcp",
            tool_catalog=[{"name": f"upstream_{number}", "description": "tool"} for number in range(100)],
        )
        integration.set_headers({"Authorization": "Bearer remote-secret"})
        integration.save()
        tools = self.rpc("tools/list").json()["result"]["tools"]
        self.assertEqual(len(tools), 8)
        self.assertNotIn("upstream_1", {tool["name"] for tool in tools})

    def test_deterministic_search_and_exact_call(self):
        integration = Integration.objects.create(
            workspace=self.user.workspace,
            name="Work",
            slug="work",
            remote_url="https://example.com/mcp",
            tool_catalog=[
                {"name": "issue_search", "description": "Search project issues", "inputSchema": {"type": "object"}},
                {"name": "project_list", "description": "List projects"},
            ],
        )
        integration.set_headers({"Authorization": "Bearer remote-secret"})
        integration.save()
        _, result = self.tool_result("gateway_search_tools", {"query": "search issue"})
        self.assertEqual([tool["name"] for tool in result["tools"]], ["work__issue_search"])
        arguments = {"q": "bug", "api_key": "tool-input-not-integration-config"}
        with patch("gateway.dispatch.call_tool", return_value={"content": [{"type": "text", "text": "done"}]}) as call:
            _, result = self.tool_result("gateway_call_tool", {"name": "work__issue_search", "arguments": arguments})
        self.assertEqual(result["content"][0]["text"], "done")
        call.assert_called_once_with(integration, "issue_search", arguments)

    def test_oauth_scopes_control_tool_calls_and_static_tokens_remain_full_access(self):
        integration = Integration.objects.create(
            workspace=self.user.workspace,
            name="Work",
            slug="work",
            remote_url="https://example.com/mcp",
            write_enabled=True,
            tool_catalog=[
                {"name": "read", "annotations": {"readOnlyHint": True}},
                {"name": "unknown"},
            ],
        )
        integration.set_headers({"Authorization": "Bearer remote-secret"})
        integration.save()
        read_token = self.oauth_token("mcp:read")
        write_token = self.oauth_token("mcp:write")

        denied_mutation, message = self.tool_result(
            "gateway_create_integration",
            {"name": "No", "slug": "no", "remote_url": "https://example.org/mcp"},
            token=read_token,
        )
        denied_unknown, _ = self.tool_result(
            "gateway_call_tool", {"name": "work__unknown", "arguments": {}}, token=read_token,
        )
        self.assertTrue(denied_mutation.json()["result"]["isError"])
        self.assertEqual(message, "This tool requires mcp:write scope.")
        self.assertTrue(denied_unknown.json()["result"]["isError"])

        with patch("gateway.dispatch.call_tool", return_value={"ok": True}) as call:
            _, read_result = self.tool_result(
                "gateway_call_tool", {"name": "work__read", "arguments": {}}, token=read_token,
            )
            _, write_result = self.tool_result(
                "gateway_call_tool", {"name": "work__unknown", "arguments": {}}, token=write_token,
            )
            _, static_result = self.tool_result(
                "gateway_call_tool", {"name": "work__unknown", "arguments": {}}, token=self.token,
            )
        self.assertEqual(read_result, {"ok": True})
        self.assertEqual(write_result, {"ok": True})
        self.assertEqual(static_result, {"ok": True})
        self.assertEqual(call.call_count, 3)

    def test_search_ranks_names_and_ignores_filler(self):
        integration = Integration.objects.create(
            workspace=self.user.workspace,
            name="Work",
            slug="work",
            remote_url="https://example.com/mcp",
            tool_catalog=[
                {"name": "issues_list", "description": "List project issues"},
                {"name": "projects_list", "description": "List accessible projects and issues"},
                {"name": "help", "description": "Please explain available operations"},
            ],
        )
        integration.set_headers({"Authorization": "Bearer remote-secret"})
        integration.save()

        queries = (
            ("issues_list", "work__issues_list"),
            ("Could you please list all project issues for me?", "work__issues_list"),
            ("please please please list issues for me", "work__issues_list"),
        )
        for query, expected in queries:
            with self.subTest(query=query):
                _, result = self.tool_result("gateway_search_tools", {"query": query})
                self.assertEqual(result["tools"][0]["name"], expected)
        _, unrelated = self.tool_result("gateway_search_tools", {"query": "quantum banana"})
        self.assertEqual(unrelated["tools"], [])

    def test_search_hides_disconnected_tools_and_handles_invalid_descriptions(self):
        disconnected = Integration.objects.create(
            workspace=self.user.workspace,
            name="Disconnected",
            slug="disconnected",
            remote_url="https://example.com/mcp",
            tool_catalog=[{"name": "issue_search", "description": "Search issues"}],
        )
        connected = Integration.objects.create(
            workspace=self.user.workspace,
            name="Connected",
            slug="connected",
            remote_url="https://example.org/mcp",
            tool_catalog=[{"name": "issue_search", "description": {"invalid": True}}],
        )
        connected.set_headers({"Authorization": "Bearer remote-secret"})
        connected.save()

        _, searched = self.tool_result("gateway_search_tools", {"query": "issue_search"})
        _, details = self.tool_result("gateway_get_integration", {"slug": disconnected.slug})

        self.assertEqual([tool["name"] for tool in searched["tools"]], ["connected__issue_search"])
        self.assertEqual(searched["tools"][0]["description"], "")
        self.assertEqual(details["tools"], [])

    def test_search_rejects_filler_only_and_filler_heavy_unrelated_queries(self):
        integration = Integration.objects.create(
            workspace=self.user.workspace,
            name="Work",
            slug="work",
            remote_url="https://example.com/mcp",
            tool_catalog=[{"name": "issues_search", "description": "Search project issues"}],
        )
        integration.set_headers({"Authorization": "Bearer remote-secret"})
        integration.save()

        for query in ("i", "could you please help me", "could you please help me find tropical weather"):
            with self.subTest(query=query):
                _, result = self.tool_result("gateway_search_tools", {"query": query})
                self.assertEqual(result["tools"], [])

    def test_mcp_integration_queries_are_workspace_scoped(self):
        other = get_user_model().objects.create_user("other")
        Integration.objects.create(
            workspace=other.workspace,
            name="Hidden",
            slug="hidden",
            remote_url="https://other.example/mcp",
            active=True,
            tool_catalog=[{"name": "secret_tool", "description": "private"}],
        )

        _, listed = self.tool_result("gateway_list_integrations")
        _, searched = self.tool_result("gateway_search_tools", {"query": "secret"})
        response, _ = self.tool_result("gateway_get_integration", {"slug": "hidden"})

        self.assertEqual(listed["integrations"], [])
        self.assertEqual(searched["tools"], [])
        self.assertTrue(response.json()["result"]["isError"])

    @override_settings(PUBLIC_BASE_URL="https://juraguard.example")
    def test_agent_create_rejects_credentials_and_returns_configured_setup_url(self):
        response, _ = self.tool_result("gateway_create_integration", {
            "name": "Unsafe", "slug": "unsafe", "remote_url": "https://example.com/mcp", "headers": {"X-Key": "secret"}
        })
        self.assertTrue(response.json()["result"]["isError"])
        self.assertFalse(Integration.objects.exists())
        _, created = self.tool_result("gateway_create_integration", {
            "name": "Safe", "slug": "safe", "remote_url": "https://example.com/mcp"
        })
        self.assertTrue(created["setup_url"].startswith("https://juraguard.example/connect/"))
        self.assertNotIn("credentials", created)

    @override_settings(PUBLIC_BASE_URL="https://juraguard.example")
    def test_agent_create_supports_provider_specific_urls(self):
        cases = (
            ({"name": "Custom", "slug": "custom", "provider_type": "generic_custom",
              "remote_url": "https://example.com/custom"}, Integration.GENERIC_CUSTOM, "remote_url"),
            ({"name": "OAuth", "slug": "oauth", "provider_type": "generic_oauth",
              "remote_url": "https://example.com/oauth"}, Integration.GENERIC_OAUTH, "remote_url"),
            ({"name": "GitLab", "slug": "gitlab", "provider_type": "gitlab",
              "base_url": "https://gitlab.com/", "write_enabled": True}, Integration.GITLAB, "base_url"),
        )
        for arguments, provider_type, url_field in cases:
            with self.subTest(provider_type=provider_type):
                _, result = self.tool_result("gateway_create_integration", arguments)
                integration = Integration.objects.get(slug=arguments["slug"])
                self.assertEqual(integration.provider_type, provider_type)
                self.assertTrue(getattr(integration, url_field))
                self.assertTrue(result["setup_url"].startswith("https://juraguard.example/connect/"))
                self.assertNotIn("secret", json.dumps(result).lower())
                if provider_type == Integration.GITLAB:
                    self.assertEqual(integration.tool_catalog, [])

    @override_settings(PUBLIC_BASE_URL="https://juraguard.example")
    def test_agent_create_rejects_wrong_url_fields_and_secrets(self):
        invalid = (
            {"name": "GitLab", "slug": "missing-base", "provider_type": "gitlab",
             "remote_url": "https://gitlab.com"},
            {"name": "OAuth", "slug": "missing-remote", "provider_type": "generic_oauth",
             "base_url": "https://example.com"},
            {"name": "Both", "slug": "both", "provider_type": "generic_custom",
             "remote_url": "https://example.com/mcp", "base_url": "https://example.com"},
            {"name": "PAT", "slug": "pat", "provider_type": "gitlab", "base_url": "https://gitlab.com",
             "pat": "never-return-this"},
            {"name": "OAuth secret", "slug": "oauth-secret", "provider_type": "generic_oauth",
             "remote_url": "https://example.com/mcp", "oauth_client_secret": "never-return-this"},
        )
        for arguments in invalid:
            with self.subTest(slug=arguments["slug"]):
                response, result = self.tool_result("gateway_create_integration", arguments)
                self.assertTrue(response.json()["result"]["isError"])
                self.assertNotIn("never-return-this", json.dumps(result))
        self.assertFalse(Integration.objects.filter(slug__in=[item["slug"] for item in invalid]).exists())

    @override_settings(PUBLIC_BASE_URL="")
    def test_agent_create_rolls_back_without_public_base_url(self):
        response, result = self.tool_result("gateway_create_integration", {
            "name": "Remote", "slug": "remote", "remote_url": "https://example.com/mcp"
        })
        self.assertTrue(response.json()["result"]["isError"])
        self.assertEqual(result, "PUBLIC_BASE_URL is required outside localhost.")
        self.assertFalse(Integration.objects.exists())

    def test_all_meta_tools_enforce_published_argument_schemas(self):
        integration = Integration.objects.create(
            workspace=self.user.workspace, name="Remote", slug="remote", remote_url="https://example.com/mcp"
        )
        invalid_calls = (
            ("gateway_search_tools", {"query": 1}),
            ("gateway_list_integrations", {"extra": True}),
            ("gateway_get_integration", {}),
            ("gateway_reconnect_integration", {"slug": "remote", "token": "secret"}),
            ("gateway_delete_integration", {"slug": "remote", "confirm": 1}),
            ("gateway_call_tool", {"name": "remote__tool", "arguments": {}, "extra": True}),
        )
        for name, arguments in invalid_calls:
            with self.subTest(name=name):
                response, _ = self.tool_result(name, arguments)
                self.assertTrue(response.json()["result"]["isError"])
        self.assertTrue(Integration.objects.filter(pk=integration.pk).exists())

    def test_update_validation_error_returns_safe_tool_error(self):
        Integration.objects.create(
            workspace=self.user.workspace, name="Remote", slug="remote", remote_url="https://example.com/mcp"
        )
        response, result = self.tool_result(
            "gateway_update_integration", {"slug": "remote", "remote_url": "not-a-url"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["result"]["isError"])
        self.assertEqual(result, "Integration details are invalid.")
