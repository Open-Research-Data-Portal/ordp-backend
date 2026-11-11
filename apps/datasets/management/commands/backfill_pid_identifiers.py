from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.datasets.models import Dataset, DatasetRelease, ResourceIdentifier
from apps.datasets.services.pid import ensure_release_identifiers, ensure_ordp_concept_identifier


class Command(BaseCommand):
    help = "Backfill ORDP-owned Concept and Version PIDs; optionally queue configured DOI registrations."

    def add_arguments(self, parser):
        parser.add_argument("--dataset-id", default=None, help="Limit to one dataset UUID.")
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        dataset_id = options["dataset_id"]
        if dataset_id:
            if not Dataset.objects.filter(pk=dataset_id).exists():
                raise CommandError(f"Dataset {dataset_id!r} was not found.")
        releases = DatasetRelease.objects.filter(
            status__in=[DatasetRelease.Status.PUBLISHED, DatasetRelease.Status.WITHDRAWN]
        ).select_related("dataset", "base_release").order_by(
            "dataset_id", "major", "minor", "patch"
        )
        if dataset_id:
            releases = releases.filter(dataset_id=dataset_id)

        published_datasets = Dataset.objects.filter(
            status=Dataset.Status.PUBLISHED
        ).order_by("pk")
        if dataset_id:
            published_datasets = published_datasets.filter(pk=dataset_id)

        inspected_datasets = published_datasets.count()
        missing_dataset_concepts = published_datasets.exclude(
            resource_identifiers__scheme=ResourceIdentifier.Scheme.ORDP,
            resource_identifiers__role=ResourceIdentifier.Role.CONCEPT,
        ).distinct().count()

        inspected = missing_concepts = missing_versions = 0
        for release in releases.iterator():
            inspected += 1
            if not ResourceIdentifier.objects.filter(
                dataset_id=release.dataset_id,
                scheme=ResourceIdentifier.Scheme.ORDP,
                role=ResourceIdentifier.Role.CONCEPT,
            ).exists():
                missing_concepts += 1
            if not ResourceIdentifier.objects.filter(
                release_id=release.pk,
                scheme=ResourceIdentifier.Scheme.ORDP,
                role=ResourceIdentifier.Role.VERSION,
            ).exists():
                missing_versions += 1

        self.stdout.write(
            f"Inspected {inspected} published/withdrawn release(s); "
            f"datasets inspected: {inspected_datasets}; missing dataset Concept PIDs: {missing_dataset_concepts}; "
            f"missing release Concept PIDs: {missing_concepts}; missing Version PIDs: {missing_versions}."
        )
        if options["dry_run"]:
            self.stdout.write("Dry run: no database changes made.")
            return

        created_or_reused = errors = 0
        for dataset in published_datasets.iterator():
            try:
                with transaction.atomic():
                    ensure_ordp_concept_identifier(dataset, creation_source="backfill")
            except Exception as exc:
                errors += 1
                self.stderr.write(f"ERROR dataset={dataset.pk}: {type(exc).__name__}: {exc}")
        for release in releases.iterator():
            try:
                with transaction.atomic():
                    ensure_release_identifiers(release)
                created_or_reused += 1
            except Exception as exc:
                errors += 1
                self.stderr.write(f"ERROR release={release.pk}: {type(exc).__name__}: {exc}")
        self.stdout.write(
            self.style.SUCCESS(
                f"Processed {created_or_reused} release(s); errors: {errors}. "
                "Existing DOI values and existing ORDP PIDs were preserved."
            )
        )
        if errors:
            raise CommandError(f"Backfill completed with {errors} error(s).")
