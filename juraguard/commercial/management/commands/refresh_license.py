from django.core.management.base import BaseCommand, CommandError

from commercial.service import LicenseServerError, refresh


class Command(BaseCommand):
    help = "Refresh the installed commercial license document."

    def handle(self, *args, **options):
        try:
            refresh()
        except LicenseServerError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(self.style.SUCCESS("License refreshed."))
