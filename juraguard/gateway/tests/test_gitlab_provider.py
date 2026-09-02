from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase

from gateway.dispatch import public_integration
from gateway.gitlab_provider import call_tool, catalog
from gateway.models import Integration
from gateway.outbound import OutboundResponse


class GitLabProviderTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("owner", password="safe-test-password")
        self.client.force_login(self.user)

    @patch("gateway.gitlab_provider.validate_pat")
    def test_pat_form_encrypts_and_never_exposes_pat(self, validate_pat):
        response = self.client.post("/integrations/add/", {
            "provider_type": Integration.GITLAB,
            "name": "Work GitLab",
            "slug": "work",
            "description": "",
            "base_url": "https://gitlab.example/",
            "remote_url": "",
            "pat": "glpat-super-secret",
            "active": "on",
        })
        self.assertRedirects(response, "/dashboard/")
        integration = Integration.objects.get()
        validate_pat.assert_called_once_with("https://gitlab.example", "glpat-super-secret")
        self.assertNotIn("glpat-super-secret", integration.encrypted_credentials)
        self.assertEqual(integration.get_credentials(), {"pat": "glpat-super-secret"})
        self.assertNotIn("pat", public_integration(integration))
        self.assertNotContains(self.client.get("/dashboard/"), "glpat-super-secret")

    def test_catalog_write_gating_and_provider_type_is_immutable(self):
        read_names = {item["name"] for item in catalog(False)}
        self.assertIn("issues_list", read_names)
        self.assertNotIn("issues_create", read_names)
        integration = Integration.objects.create(
            workspace=self.user.workspace, name="GitLab", slug="gitlab", provider_type=Integration.GITLAB,
            base_url="https://gitlab.example", write_enabled=False,
        )
        integration.set_credentials({"pat": "secret"})
        integration.tool_catalog = catalog(False)
        integration.save()
        with self.assertRaisesMessage(ValueError, "write access is not granted"):
            call_tool(integration, "issues_create", {"project": 1, "title": "Bug"})
        response = self.client.post(f"/integrations/{integration.pk}/edit/", {
            "provider_type": Integration.GENERIC_CUSTOM,
            "name": "GitLab", "slug": "gitlab", "description": "", "base_url": "https://gitlab.example",
            "remote_url": "https://evil.example/mcp", "active": "on",
        })
        self.assertRedirects(response, "/dashboard/")
        integration.refresh_from_db()
        self.assertEqual(integration.provider_type, Integration.GITLAB)

    @patch("gateway.gitlab_provider.validate_pat")
    def test_base_url_change_requires_and_validates_replacement_pat(self, validate_pat):
        integration = Integration.objects.create(
            workspace=self.user.workspace, name="GitLab", slug="gitlab", provider_type=Integration.GITLAB,
            base_url="https://old.example",
        )
        integration.set_credentials({"pat": "old-secret"})
        integration.save()
        form = {
            "provider_type": Integration.GITLAB, "name": "GitLab", "slug": "gitlab", "description": "",
            "base_url": "https://new.example/", "remote_url": "", "active": "on",
        }
        rejected = self.client.post(f"/integrations/{integration.pk}/edit/", form)
        self.assertContains(rejected, "Enter a new personal access token", status_code=200)
        validate_pat.assert_not_called()
        integration.refresh_from_db()
        self.assertEqual(integration.base_url, "https://old.example")
        self.assertEqual(integration.get_credentials()["pat"], "old-secret")

        accepted = self.client.post(
            f"/integrations/{integration.pk}/edit/", {**form, "pat": "new-secret"},
        )
        self.assertRedirects(accepted, "/dashboard/")
        validate_pat.assert_called_once_with("https://new.example", "new-secret")
        integration.refresh_from_db()
        self.assertEqual(integration.get_credentials()["pat"], "new-secret")

    @patch("gateway.gitlab_provider.request")
    def test_dispatch_constructs_private_token_and_redacts_response(self, mocked):
        integration = Integration.objects.create(
            workspace=self.user.workspace, name="GitLab", slug="gitlab", provider_type=Integration.GITLAB,
            base_url="https://gitlab.example", write_enabled=True,
        )
        integration.set_credentials({"pat": "secret-pat"})
        integration.save()
        remote_response = MagicMock()
        remote_response.json.return_value = {"id": 1, "private_token": "must-not-leak", "nested": {"token": "no"}}
        mocked.return_value = remote_response
        result = call_tool(integration, "projects_get", {"project": "group/project"})
        self.assertEqual(result, {"id": 1, "nested": {}})
        self.assertEqual(mocked.call_args.kwargs["headers"]["PRIVATE-TOKEN"], "secret-pat")
        self.assertIn("group%2Fproject", mocked.call_args.args[0])

    def test_dispatch_accepts_list_responses_but_rejects_scalars(self):
        integration = Integration.objects.create(
            workspace=self.user.workspace, name="GitLab", slug="gitlab", provider_type=Integration.GITLAB,
            base_url="https://gitlab.example",
        )
        integration.set_credentials({"pat": "secret-pat"})
        integration.save()

        with patch("gateway.gitlab_provider.request", return_value=OutboundResponse(200, {}, b'[{"id": 1}]')):
            self.assertEqual(call_tool(integration, "projects_list", {}), [{"id": 1}])
        with patch("gateway.gitlab_provider.request", return_value=OutboundResponse(200, {}, b'"invalid"')):
            with self.assertRaisesMessage(ValueError, "invalid JSON"):
                call_tool(integration, "projects_list", {})
