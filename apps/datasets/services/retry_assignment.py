from apps.datasets.models import Dataset
from apps.datasets.services.assignment import assign_reviewers


def retry_pending_assignments():
    datasets = (
        Dataset.objects
        .filter(
            status=Dataset.Status.PENDING,
            is_active=True,
        )
        .filter(reviewer_assignments__isnull=True)
        .distinct()
        .order_by('created_at')
    )

    assigned_count = 0

    for dataset in datasets:
        assignments = assign_reviewers(dataset)
        if len(assignments) >= 3:
            assigned_count += 1

    return assigned_count