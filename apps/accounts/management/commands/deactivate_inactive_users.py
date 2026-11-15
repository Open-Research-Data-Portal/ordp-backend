from django.core.management.base import BaseCommand

from apps.accounts.services import deactivate_inactive_users


class Command(BaseCommand):
    help = "Deactivate accounts with no successful login in the past six calendar months."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        result = deactivate_inactive_users(dry_run=options["dry_run"])
        verb = "Would deactivate" if options["dry_run"] else "Deactivated"
        self.stdout.write(
            self.style.SUCCESS(
                f"{verb} {result['affected_count']} inactive account(s). "
                f"Cutoff: {result['cutoff'].isoformat()}"
            )
        )