from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class WorkspaceMigrationTests(TransactionTestCase):
    migrate_from = [("gateway", "0003_oauthaccesstoken_family_id_oauthclient_expires_at_and_more")]
    migrate_to = [("gateway", "0006_enforce_workspace_ownership")]

    def setUp(self):
        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_from)
        old_apps = executor.loader.project_state(self.migrate_from).apps
        User = old_apps.get_model("auth", "User")
        Integration = old_apps.get_model("gateway", "Integration")
        owner = User.objects.create(username="existing-owner")
        Integration.objects.create(
            user_id=owner.id,
            name="Existing",
            slug="existing",
            remote_url="https://example.com/mcp",
        )

        executor = MigrationExecutor(connection)
        executor.migrate(self.migrate_to)
        self.apps = executor.loader.project_state(self.migrate_to).apps

    def tearDown(self):
        MigrationExecutor(connection).migrate(MigrationExecutor(connection).loader.graph.leaf_nodes())
        super().tearDown()

    def test_existing_owner_and_integration_move_to_personal_workspace(self):
        Workspace = self.apps.get_model("gateway", "Workspace")
        Integration = self.apps.get_model("gateway", "Integration")

        workspace = Workspace.objects.get(owner__username="existing-owner")
        self.assertEqual(Integration.objects.get(slug="existing").workspace_id, workspace.id)
