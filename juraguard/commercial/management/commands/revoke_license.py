from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from commercial.models import IssuedLicense


class Command(BaseCommand):
    help = "Revoke a commercial license by its safe ID."

    def add_arguments(self, parser):
        parser.add_argument("license_id")

    def handle(self, *args, **options):
        if settings.DEPLOYMENT_MODE != "cloud":
            raise CommandError("Licenses can only be revoked in cloud mode.")
        updated = IssuedLicense.objects.filter(pk=options["license_id"], revoked_at__isnull=True).update(
            revoked_at=timezone.now()
        )
        if not updated:
            raise CommandError("Active license not found.")
        self.stdout.write(self.style.SUCCESS("License revoked."))
