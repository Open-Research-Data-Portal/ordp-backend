import random
from django.db.models import Count, Q

from apps.accounts.models import ActivityLog, UserProfile, UserRole
from apps.admin_panel.models import DatasetReviewerAssignment
from apps.notifications.services import notify
from apps.notifications.models import Notification

MIN_REVIEWERS = 2
MAX_REVIEWERS = 3


def _create_assignment(dataset, reviewer_profile):
    assignment = DatasetReviewerAssignment.objects.create(
        dataset=dataset,
        reviewer=reviewer_profile.user,
    )
    notify(
        user=reviewer_profile.user,
        notification_type=Notification.NotificationType.DATASET_ASSIGNED_FOR_REVIEW,
        message=f'You have been assigned to review "{dataset.title}".',
        dataset=dataset,
        link_path=f"/admin-panel/queue/{dataset.id}",
    )
    return assignment


def assign_reviewers(dataset):
    """
    Assign reviewers to a dataset.

    The workflow needs at least two eligible reviewers before moderation can
    begin, and uses up to three reviewers when available.
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

    # We need the minimum reviewer count before moderation can begin.
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
            selected = preferred[:MAX_REVIEWERS]
        else:
            # Not enough category-matched reviewers; use all eligible
            # reviewers while still requiring the minimum total.
            all_candidates = get_least_loaded(base)
            selected = all_candidates[:MAX_REVIEWERS]
    else:
        selected = get_least_loaded(base)[:MAX_REVIEWERS]

    if len(selected) < MIN_REVIEWERS:
        dataset.assigned_reviewer = None
        dataset.save(update_fields=["assigned_reviewer"])
        return []

    assignments = []

    for profile in selected:
        assignments.append(_create_assignment(dataset, profile))

    dataset.assigned_reviewer = selected[0].user
    dataset.save(update_fields=["assigned_reviewer"])

    return assignments
def top_up_reviewers(dataset):
    """
    Called after a reviewer is revoked mid-review. Fills only the
    remaining slots needed to reach MAX_REVIEWERS, rather than
    reassigning a fresh set of reviewers. Returns the list of new assignments
    (empty if there aren't enough eligible reviewers left — the dataset
    is simply left short, which already shows up in the moderation
    queue for any admin to see).
    """
    from ..models import Dataset

    already_assigned = list(
        DatasetReviewerAssignment.objects.filter(dataset=dataset).values_list("reviewer_id", flat=True)
    )
    needed = MAX_REVIEWERS - len(already_assigned)
    if needed <= 0:
        return []

    category = getattr(getattr(dataset, "metadata", None), "category", None)

    base = (
        UserProfile.objects
        .filter(roles__role=UserRole.RoleChoice.REVIEWER)
        .exclude(user_id=dataset.owner_id)
        .exclude(user_id__in=already_assigned)
        .distinct()
    )

    def get_least_loaded(queryset, limit):
        candidates = list(
            queryset.annotate(
                pending_count=Count(
                    "user__assigned_datasets",
                    filter=Q(user__assigned_datasets__status=Dataset.Status.PENDING),
                )
            )
        )
        if not candidates:
            return []
        candidates.sort(key=lambda profile: profile.pending_count)
        min_load = candidates[0].pending_count
        least_loaded = [p for p in candidates if p.pending_count == min_load]
        random.shuffle(least_loaded)
        return least_loaded[:limit]

    selected = []
    if category is not None:
        selected = get_least_loaded(base.filter(interests=category), needed)
    if len(selected) < needed:
        remaining_needed = needed - len(selected)
        already_picked_ids = {p.user_id for p in selected}
        fallback = get_least_loaded(base.exclude(user_id__in=already_picked_ids), remaining_needed)
        selected += fallback

    return [_create_assignment(dataset, profile) for profile in selected]
