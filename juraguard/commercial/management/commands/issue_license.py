import secrets
from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from commercial.models import IssuedLicense
from gateway.security import opaque_hash


class Command(BaseCommand):
    help = "Issue a commercial license and print its key once."

    def add_arguments(self, parser):
        parser.add_argument("organization")
        parser.add_argument("--entitlement", action="append", required=True)
        parser.add_argument("--days", type=int, default=365)

    def handle(self, *args, **options):
        if settings.DEPLOYMENT_MODE != "cloud":
            raise CommandError("Licenses can only be issued in cloud mode.")
        entitlements = sorted(set(options["entitlement"]))
        if not entitlements or not set(entitlements) <= settings.LICENSE_ENTITLEMENTS:
            raise CommandError("Unsupported entitlement.")
        if options["days"] < 1:
            raise CommandError("--days must be positive.")
        key = f"jgl_{secrets.token_urlsafe(32)}"
        record = IssuedLicense.objects.create(
            key_hash=opaque_hash(key),
            organization=options["organization"],
            entitlements=entitlements,
            expires_at=timezone.now() + timedelta(days=options["days"]),
        )
        self.stdout.write(f"License ID: {record.pk}")
        self.stdout.write(f"License key (shown once): {key}")
