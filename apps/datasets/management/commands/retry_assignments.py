from django.core.management.base import BaseCommand

from apps.datasets.services.retry_assignment import retry_pending_assignments


class Command(BaseCommand):
    help = (
        "Retries reviewer assignment for pending datasets that have no "
        "reviewer_assignments yet. Safe to run repeatedly — datasets that "
        "already have assignments, or still don't have 3 eligible reviewers, "
        "are left untouched."
    )

    def handle(self, *args, **options):
        assigned_count = retry_pending_assignments()
        self.stdout.write(
            self.style.SUCCESS(
                f"Retry complete: {assigned_count} dataset(s) newly assigned to reviewers."
            )
        )