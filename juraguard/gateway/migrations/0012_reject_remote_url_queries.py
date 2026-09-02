from urllib.parse import urlsplit, urlunsplit

from django.db import migrations, models

import gateway.security


def remove_existing_queries(apps, _schema_editor):
    Integration = apps.get_model("gateway", "Integration")
    reset_fields = {
        "active": False,
        "encrypted_headers": "",
        "encrypted_credentials": "",
        "encrypted_oauth_state": "",
        "tool_catalog": [],
        "catalog_updated_at": None,
    }
    for integration in Integration.objects.exclude(remote_url="").iterator(chunk_size=500):
        parsed = urlsplit(integration.remote_url)
        if parsed.query:
            integration.remote_url = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", parsed.fragment))
            for field, value in reset_fields.items():
                setattr(integration, field, value)
            integration.save(update_fields=["remote_url", *reset_fields])
    for integration in Integration.objects.exclude(base_url="").iterator(chunk_size=500):
        parsed = urlsplit(integration.base_url)
        if parsed.query:
            integration.base_url = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", parsed.fragment))
            for field, value in reset_fields.items():
                setattr(integration, field, value)
            integration.save(update_fields=["base_url", *reset_fields])


class Migration(migrations.Migration):
    dependencies = [("gateway", "0011_merge_20260831_1612")]

    operations = [
        migrations.RunPython(remove_existing_queries, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="integration",
            name="base_url",
            field=models.URLField(blank=True, max_length=500, validators=[gateway.security.validate_remote_url_syntax]),
        ),
        migrations.AlterField(
            model_name="integration",
            name="remote_url",
            field=models.URLField(blank=True, max_length=500, validators=[gateway.security.validate_remote_url_syntax]),
        ),
    ]
