from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase

from gateway.forms import IntegrationForm
from gateway.hetzner_provider import TOOLS, call_tool, catalog, validate_credentials
from gateway.models import Integration, SetupLink, personal_workspace
from gateway.outbound import OutboundResponse
from gateway.provider_types import BuiltinProvider, CredentialField
from gateway.providers import get_provider, provider_choices, providers
from gateway.tool_defs import META_TOOLS


class ProviderRegistryTests(TestCase):
    def test_builtin_registry_exposes_contract_and_defaults(self):
        self.assertEqual(set(providers()), {"gitlab", "hetzner"})
        for provider in providers().values():
            self.assertIsInstance(provider, BuiltinProvider)
            self.assertTrue(provider.label)
            self.assertTrue(provider.default_base_url)
            self.assertTrue(provider.credential_fields)
            self.assertTrue(callable(provider.catalog))
            self.assertTrue(callable(provider.test_connection))
            self.assertTrue(callable(provider.call_tool))
        self.assertEqual(get_provider("hetzner").default_base_url, "https://api.hetzner.cloud/v1")

    def test_provider_metadata_drives_choices_schema_and_secret_fields(self):
        choice_keys = {key for key, _ in provider_choices()}
        create_schema = next(tool for tool in META_TOOLS if tool["name"] == "gateway_create_integration")
        self.assertEqual(set(create_schema["inputSchema"]["properties"]["provider_type"]["enum"]), choice_keys)

        with patch(
            "gateway.hetzner_provider.HetznerProvider.credential_fields",
            (CredentialField("project_token", "Project token", "Provider-defined help."),),
        ):
            form = IntegrationForm()
        self.assertNotIn("api_token", form.fields)
        self.assertEqual(form.fields["project_token"].label, "Project token")
        self.assertEqual(form.fields["project_token"].help_text, "Provider-defined help.")
        self.assertEqual(form.fields["project_token"].provider_fields, "hetzner")


class HetznerProviderTests(TestCase):
    def setUp(self):
        user = get_user_model().objects.create_user("hetzner-user")
        self.integration = Integration.objects.create(
            workspace=personal_workspace(user), name="Hetzner", slug="hetzner", provider_type=Integration.HETZNER,
            base_url="https://api.hetzner.cloud/v1",
        )
        self.integration.set_credentials({"api_token": "super-secret-token"})

    def test_catalog_inventory_write_gating_and_destructive_annotations(self):
        readonly = {tool["name"] for tool in catalog(False)}
        writable = {tool["name"] for tool in catalog(True)}
        for resource in (
            "actions", "servers", "volumes", "networks", "firewalls", "floating_ips", "primary_ips",
            "ssh_keys", "images", "locations", "server_types",
        ):
            self.assertIn(f"{resource}_list", readonly)
        self.assertNotIn("servers_create", readonly)
        self.assertIn("servers_create", writable)
        self.assertTrue(TOOLS["servers_delete"]["annotations"]["destructiveHint"])
        self.assertTrue(TOOLS["servers_reset"]["annotations"]["destructiveHint"])
        self.assertTrue(TOOLS["servers_rebuild"]["annotations"]["destructiveHint"])
        self.assertTrue(TOOLS["servers_disable_backup"]["annotations"]["destructiveHint"])
        for name in (
            "servers_reset_password", "servers_enable_backup", "servers_disable_backup",
            "servers_attach_to_network", "servers_detach_from_network", "servers_change_alias_ips",
            "networks_add_subnet", "networks_delete_subnet",
        ):
            self.assertIn(name, writable)
        for inaccurate_name in (
            "networks_attach_to_server", "networks_detach_from_server",
            "networks_add_ip_range", "networks_delete_ip_range",
        ):
            self.assertNotIn(inaccurate_name, writable)

    @patch("gateway.hetzner_provider.request")
    def test_validation_uses_bearer_auth_and_safe_error(self, request):
        request.return_value = OutboundResponse(200, {}, b'{"servers": []}')
        validate_credentials(self.integration.base_url, {"api_token": "super-secret-token"})
        self.assertEqual(request.call_args.kwargs["headers"]["Authorization"], "Bearer super-secret-token")

        request.side_effect = ValueError("super-secret-token")
        with self.assertRaisesMessage(ValidationError, "Hetzner Cloud connection failed") as error:
            validate_credentials(self.integration.base_url, {"api_token": "super-secret-token"})
        self.assertNotIn("super-secret-token", str(error.exception))

    def test_direct_write_call_is_blocked(self):
        with self.assertRaisesMessage(ValueError, "write access is not granted"):
            call_tool(self.integration, "servers_delete", {"id": 12})

    @patch("gateway.hetzner_provider.request")
    def test_request_and_response_handling(self, request):
        self.integration.write_enabled = True
        request.return_value = OutboundResponse(202, {}, b'{"action": {"id": 8}}')
        result = call_tool(self.integration, "servers_reboot", {"id": 12})
        self.assertEqual(result, {"action": {"id": 8}})
        self.assertEqual(request.call_args.args[0], "https://api.hetzner.cloud/v1/servers/12/actions/reboot")
        self.assertEqual(request.call_args.kwargs["method"], "POST")
        self.assertEqual(request.call_args.kwargs["headers"]["Authorization"], "Bearer super-secret-token")

        request.return_value = OutboundResponse(204, {}, b"")
        self.assertEqual(call_tool(self.integration, "ssh_keys_delete", {"id": 3}), {"success": True})

    @patch("gateway.hetzner_provider.request")
    def test_corrected_network_action_paths(self, request):
        self.integration.write_enabled = True
        request.return_value = OutboundResponse(202, {}, b'{"action": {"id": 8}}')

        call_tool(self.integration, "networks_add_subnet", {"id": 4, "payload": {"type": "cloud"}})
        self.assertEqual(
            request.call_args.args[0],
            "https://api.hetzner.cloud/v1/networks/4/actions/add_subnet",
        )
        call_tool(self.integration, "servers_attach_to_network", {"id": 12, "payload": {"network": 4}})
        self.assertEqual(
            request.call_args.args[0],
            "https://api.hetzner.cloud/v1/servers/12/actions/attach_to_network",
        )

    @patch("gateway.hetzner_provider.request")
    def test_setup_and_dashboard_connection_test(self, request):
        request.return_value = OutboundResponse(200, {}, b'{"servers": []}')
        user = self.integration.workspace.owner
        self.client.force_login(user)
        response = self.client.post("/integrations/add/", {
            "provider_type": "hetzner", "name": "Cloud", "slug": "cloud", "description": "",
            "base_url": "", "remote_url": "", "api_token": "new-secret", "active": "on",
        })
        self.assertRedirects(response, "/dashboard/")
        created = Integration.objects.get(slug="cloud")
        self.assertEqual(created.base_url, "https://api.hetzner.cloud/v1")
        self.assertEqual(created.get_credentials(), {"api_token": "new-secret"})

        response = self.client.post(f"/integrations/{created.pk}/test/", follow=True)
        self.assertContains(response, "Hetzner Cloud connection succeeded")
        self.assertContains(response, "Hetzner Cloud actions ready")
        self.assertContains(response, "cloud__servers_list")
        self.assertContains(response, "data-copy")

    @patch("gateway.hetzner_provider.request")
    def test_private_setup_link_encrypts_token_and_builds_catalog(self, request):
        request.return_value = OutboundResponse(200, {}, b'{"servers": []}')
        disconnected = Integration.objects.create(
            workspace=self.integration.workspace, name="Setup", slug="setup", provider_type=Integration.HETZNER,
            base_url="https://api.hetzner.cloud/v1",
        )
        token = SetupLink.issue(disconnected)
        response = self.client.post(f"/connect/{token}/", {"api_token": "setup-secret"})
        self.assertContains(response, "Hetzner Cloud actions ready")
        disconnected.refresh_from_db()
        self.assertEqual(disconnected.get_credentials(), {"api_token": "setup-secret"})
        self.assertNotIn("setup-secret", disconnected.encrypted_credentials)
        self.assertIn("servers_list", {tool["name"] for tool in disconnected.tool_catalog})
