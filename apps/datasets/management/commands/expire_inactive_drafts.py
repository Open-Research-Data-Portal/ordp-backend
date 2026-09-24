from django.core.management.base import BaseCommand

from apps.datasets.services.draft_expiration import expire_inactive_drafts


class Command(BaseCommand):
    help = "Delete inactive draft datasets after the configured expiration period."

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=None)
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        result = expire_inactive_drafts(days=options["days"], dry_run=options["dry_run"])
        self.stdout.write(
            self.style.SUCCESS(
                f"Expired drafts: {result['expired_count']}; deleted: {result['deleted_count']}"
            )
        )
