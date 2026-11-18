from django.core.management.base import BaseCommand, CommandError

from apps.datasets.services.legacy_baseline import backfill_legacy_baseline_releases


class Command(BaseCommand):
    help = (
        "Create immutable 1.0.0 V2 baseline releases for published V1 datasets "
        "whose complete file manifests can be verified in object storage."
    )

    def add_arguments(self, parser):
        parser.add_argument("--dataset-id", default=None)
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        result = backfill_legacy_baseline_releases(
            dataset_id=options["dataset_id"],
            dry_run=options["dry_run"],
        )
        action = "Would create" if options["dry_run"] else "Created"
        count = result["would_create_count"] if options["dry_run"] else result["created_count"]
        self.stdout.write(
            f"Scanned: {result['scanned_count']}; {action.lower()}: {count}; "
            f"skipped: {result['skipped_count']}; "
            f"unverifiable: {len(result['unverifiable_dataset_ids'])}."
        )
        for dataset_id in result["eligible_dataset_ids"]:
            self.stdout.write(f"Eligible baseline dataset: {dataset_id}")
        for dataset_id in result["already_baselined_ids"]:
            self.stdout.write(f"Already baselined: {dataset_id}")
        for dataset_id in result["unverifiable_dataset_ids"]:
            self.stderr.write(self.style.ERROR(f"Unverifiable dataset: {dataset_id}"))
        for error in result["errors"]:
            self.stderr.write(self.style.ERROR(error))
        if result["errors"]:
            raise CommandError(
                "One or more datasets could not be baselined. No unverified release was created."
            )
