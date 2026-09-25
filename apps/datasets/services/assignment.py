import random
from django.db.models import Count, Q

from apps.accounts.models import ActivityLog, UserProfile, UserRole
from apps.admin_panel.models import DatasetReviewerAssignment
from apps.notifications.services import notify
from apps.notifications.models import Notification

MIN_REVIEWERS = 3


def assign_reviewers(dataset):
    """
    Assign exactly 3 eligible reviewers to a dataset.

    If fewer than 3 eligible reviewers exist, the dataset remains pending
    without reviewer assignments. It can be assigned later when enough
    reviewers become available.
    """
    from ..models import Dataset, Contributor

    category = getattr(getattr(dataset, "metadata", None), "category", None)

    coauthor_user_ids = (
        Contributor.objects
        .filter(dataset=dataset, contributor_type=Contributor.ContributorType.CO_AUTHOR)
        .exclude(user_id=None)
        .values_list("user_id", flat=True)
    )
    major_change_user_ids = (
        dataset.versions
        .exclude(changed_by_id=None)
        .values_list("changed_by_id", flat=True)
    )
    minor_change_user_ids = (
        ActivityLog.objects
        .filter(action="minor_revision_applied", target_object=f"Dataset:{dataset.id}")
        .exclude(user_id=None)
        .values_list("user_id", flat=True)
    )

    base = (
        UserProfile.objects
        .filter(roles__role=UserRole.RoleChoice.REVIEWER)
        .exclude(user_id=dataset.owner_id)
        .exclude(user_id__in=coauthor_user_ids)
        .exclude(user_id__in=major_change_user_ids)
        .exclude(user_id__in=minor_change_user_ids)
        .exclude(user__password__startswith="!")
        .distinct()
    )

    # Never assign the same reviewer twice.
    already_assigned = DatasetReviewerAssignment.objects.filter(
        dataset=dataset
    ).values_list("reviewer_id", flat=True)

    base = base.exclude(user_id__in=already_assigned)

    # We need three reviewers before moderation can begin.
    if base.count() < MIN_REVIEWERS:
        dataset.assigned_reviewer = None
        dataset.save(update_fields=["assigned_reviewer"])
        return []

    def get_least_loaded(queryset):
        candidates = list(
            queryset.annotate(
                pending_count=Count(
                    "user__assigned_datasets",
                    filter=Q(
                        user__assigned_datasets__status=Dataset.Status.PENDING
                    ),
                )
            )
        )

        if not candidates:
            return []

        candidates.sort(key=lambda profile: profile.pending_count)

        # Get everyone tied at the lowest workload, then randomly select
        # from that group until we have three reviewers.
        min_load = candidates[0].pending_count
        least_loaded = [
            profile
            for profile in candidates
            if profile.pending_count == min_load
        ]

        random.shuffle(least_loaded)

        return least_loaded

    # Prefer reviewers who are interested in the dataset category.
    if category is not None:
        preferred = get_least_loaded(base.filter(interests=category))

        if len(preferred) >= MIN_REVIEWERS:
            selected = preferred[:MIN_REVIEWERS]
        else:
            # Not enough category-matched reviewers; use all eligible
            # reviewers while still requiring three total.
            all_candidates = get_least_loaded(base)
            selected = all_candidates[:MIN_REVIEWERS]
    else:
        selected = get_least_loaded(base)[:MIN_REVIEWERS]

    if len(selected) < MIN_REVIEWERS:
        dataset.assigned_reviewer = None
        dataset.save(update_fields=["assigned_reviewer"])
        return []

    assignments = []

    for profile in selected:
        assignment = DatasetReviewerAssignment.objects.create(
            dataset=dataset,
            reviewer=profile.user,
        )
        assignments.append(assignment)

        notify(
            user=profile.user,
            notification_type=Notification.NotificationType.DATASET_ASSIGNED_FOR_REVIEW,
            message=f'You have been assigned to review the dataset "{dataset.title}".',
            dataset=dataset,
            link_path=f"/datasets/{dataset.id}",
        )

    dataset.assigned_reviewer = selected[0].user
    dataset.save(update_fields=["assigned_reviewer"])

    return assignments