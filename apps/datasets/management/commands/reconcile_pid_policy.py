from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.datasets.models import (
    Dataset, DatasetRelease, OutboxEvent,
    ResourceIdentifier,
)
from apps.datasets.services.pid import ensure_release_identifiers


class Command(BaseCommand):
    help = (
        "Reconcile historical concept/version PID records and pending registration "
        "events with ORDP_DOI_REGISTRATION_POLICY without deleting identifiers."
    )

    def add_arguments(self, parser):
        parser.add_argument("--dataset-id")
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        policy = getattr(settings, "ORDP_DOI_REGISTRATION_POLICY", "dataset_and_release")
        allowed = {"dataset_only", "release_only", "dataset_and_release", "disabled"}
        if policy not in allowed:
            raise CommandError(f"Invalid ORDP_DOI_REGISTRATION_POLICY={policy!r}.")

        datasets = Dataset.objects.filter(
            releases__status__in=[DatasetRelease.Status.PUBLISHED, DatasetRelease.Status.WITHDRAWN]
        ).distinct().order_by("pk")
        if options["dataset_id"]:
            datasets = datasets.filter(pk=options["dataset_id"])

        summary = {
            "datasets": 0, "identifiers_preserved": 0,
            "events_cancelled": 0, "releases_processed": 0,
            "identifiers_to_create": 0, "out_of_policy_events": 0,
        }
        for dataset in datasets.iterator():
            with transaction.atomic():
                locked_dataset = Dataset.objects.select_for_update().get(pk=dataset.pk)
                releases = list(locked_dataset.releases.filter(
                    status__in=[DatasetRelease.Status.PUBLISHED, DatasetRelease.Status.WITHDRAWN]
                ).order_by("major", "minor", "patch"))
                summary["datasets"] += 1
                summary["releases_processed"] += len(releases)
                allowed_roles = {
                    "dataset_only": {ResourceIdentifier.Role.CONCEPT},
                    "release_only": {ResourceIdentifier.Role.VERSION},
                    "dataset_and_release": {ResourceIdentifier.Role.CONCEPT, ResourceIdentifier.Role.VERSION},
                    "disabled": set(),
                }[policy]

                identifiers = list(ResourceIdentifier.objects.filter(
                    dataset=locked_dataset
                ) | ResourceIdentifier.objects.filter(release__dataset=locked_dataset))
                summary["identifiers_preserved"] += len(identifiers)
                existing_concept = next((item for item in identifiers if item.role == ResourceIdentifier.Role.CONCEPT), None)
                if (
                    ResourceIdentifier.Role.CONCEPT in allowed_roles
                    and any(item.status == DatasetRelease.Status.PUBLISHED for item in releases)
                    and existing_concept is None
                ):
                    summary["identifiers_to_create"] += 1
                for release in releases:
                    if release.status != DatasetRelease.Status.PUBLISHED:
                        continue
                    has_version = any(
                        item.role == ResourceIdentifier.Role.VERSION and item.release_id == release.pk
                        for item in identifiers
                    )
                    if ResourceIdentifier.Role.VERSION in allowed_roles and not has_version:
                        summary["identifiers_to_create"] += 1
                out_of_policy = [item for item in identifiers if item.role not in allowed_roles]
                for identifier in out_of_policy:
                    summary["out_of_policy_events"] += OutboxEvent.objects.filter(
                        idempotency_key=f"pid-register:{identifier.pk}",
                        status__in=[OutboxEvent.Status.PENDING, OutboxEvent.Status.FAILED],
                    ).count()
                if options["dry_run"]:
                    continue

                # Ensure only policy-selected scopes for all published releases.
                if policy != "disabled":
                    for release in releases:
                        if release.status == DatasetRelease.Status.PUBLISHED:
                            ensure_release_identifiers(release)

                # Out-of-policy identifiers are retained. Only unregistered,
                # non-processing queue work is cancelled; registered identifiers
                # and in-flight leases are never rewritten by reconciliation.
                for identifier in identifiers:
                    if identifier.role in allowed_roles:
                        continue
                    if identifier.registration_status == ResourceIdentifier.RegistrationStatus.REGISTERED:
                        continue
                    if identifier.registration_status in {
                        ResourceIdentifier.RegistrationStatus.PENDING,
                        ResourceIdentifier.RegistrationStatus.NOT_REQUESTED,
                    }:
                        identifier.registration_status = ResourceIdentifier.RegistrationStatus.NOT_REQUESTED
                        identifier.save(update_fields=["registration_status", "updated_at"])
                    event_qs = OutboxEvent.objects.filter(
                        idempotency_key=f"pid-register:{identifier.pk}",
                        status__in=[OutboxEvent.Status.PENDING, OutboxEvent.Status.FAILED],
                    )
                    summary["events_cancelled"] += event_qs.count()
                    event_qs.update(
                        status=OutboxEvent.Status.CANCELLED,
                        failed_at=None,
                        last_error=f"Cancelled by PID policy reconciliation ({policy}).",
                        locked_at=None, locked_until=None, locked_by=None,
                    )

        mode = "Dry run" if options["dry_run"] else "Reconciled"
        self.stdout.write(
            f"{mode} policy={policy}: datasets={summary['datasets']}, "
            f"releases={summary['releases_processed']}, "
            f"identifiers_preserved={summary['identifiers_preserved']}, "
            f"identifiers_to_create_estimate={summary['identifiers_to_create']}, "
            f"out_of_policy_events={summary['out_of_policy_events']}, "
            f"events_cancelled={summary['events_cancelled']}."
        )
