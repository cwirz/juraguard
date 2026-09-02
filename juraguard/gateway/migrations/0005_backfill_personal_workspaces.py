from django.conf import settings
from django.db import migrations


def backfill_personal_workspaces(apps, schema_editor):
    User = apps.get_model(*settings.AUTH_USER_MODEL.split("."))
    Workspace = apps.get_model("gateway", "Workspace")
    Integration = apps.get_model("gateway", "Integration")

    for user_id in User.objects.values_list("id", flat=True).iterator():
        workspace, _ = Workspace.objects.get_or_create(owner_id=user_id)
        Integration.objects.filter(user_id=user_id, workspace__isnull=True).update(workspace_id=workspace.id)


class Migration(migrations.Migration):
    dependencies = [("gateway", "0004_add_workspaces")]

    operations = [migrations.RunPython(backfill_personal_workspaces, migrations.RunPython.noop)]
