from django.core.management.base import BaseCommand, CommandError

from apps.datasets.services.legacy_backfill import (
    backfill_legacy_stored_objects,
)


class Command(BaseCommand):
    help = (
        "Hash legacy dataset files from configured object storage "
        "and link verified StoredObjects."
    )

    def add_arguments(self, parser):
        parser.add_argument("--dataset-id", default=None)
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        result = backfill_legacy_stored_objects(
            dataset_id=options["dataset_id"],
            dry_run=options["dry_run"],
        )

        count_label = "Would link" if options["dry_run"] else "Linked"
        count = (
            result["would_link_count"]
            if options["dry_run"]
            else result["linked_count"]
        )

        self.stdout.write(
            f"Scanned: {result['scanned_count']}; "
            f"{count_label.lower()}: {count}; "
            f"skipped: {result['skipped_count']}; "
            f"errors: {len(result['errors'])}"
        )

        for error in result["errors"]:
            self.stderr.write(self.style.ERROR(error))

        if result["errors"]:
            raise CommandError(
                f"Backfill left {len(result['errors'])} file(s) "
                "unmodified; see errors above."
            )