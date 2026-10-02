from django.db.models import Count

from apps.datasets.models import Dataset
from apps.datasets.services.assignment import MAX_REVIEWERS, MIN_REVIEWERS, assign_reviewers, top_up_reviewers


def retry_pending_assignments():
    datasets = (
        Dataset.objects
        .filter(
            status=Dataset.Status.PENDING,
            is_active=True,
        )
        .annotate(assigned_count=Count("reviewer_assignments", distinct=True))
        .filter(assigned_count__lt=MAX_REVIEWERS)
        .distinct()
        .order_by('created_at')
    )

    assigned_count = 0

    for dataset in datasets:
        if dataset.assigned_count:
            assignments = top_up_reviewers(dataset)
        else:
            assignments = assign_reviewers(dataset)
        if len(assignments) and dataset.reviewer_assignments.count() >= MIN_REVIEWERS:
            assigned_count += 1

    return assigned_count
