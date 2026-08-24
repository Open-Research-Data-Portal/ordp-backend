import random
from django.db.models import Count, Q
from apps.accounts.models import UserProfile, UserRole


def assign_reviewer(dataset):
    """Assign a checker/admin, never the dataset owner, even if the owner has
    multiple roles (for example both researcher and checker)."""
    from ..models import Dataset

    category = getattr(getattr(dataset, "metadata", None), "category", None)

    def eligible(qs):
        return qs.exclude(user_id=dataset.owner_id).distinct()

    def least_loaded(qs):
        candidates = list(
            eligible(qs).annotate(
                pending_count=Count(
                    "user__assigned_datasets",
                    filter=Q(user__assigned_datasets__status=Dataset.Status.PENDING),
                )
            )
        )
        if not candidates:
            return None
        min_load = min(c.pending_count for c in candidates)
        return random.choice([c for c in candidates if c.pending_count == min_load])

    reviewer_roles = [UserRole.RoleChoice.CHECKER, UserRole.RoleChoice.ADMIN]
    base = UserProfile.objects.filter(roles__role__in=reviewer_roles)

    chosen_profile = None
    if category is not None:
        chosen_profile = least_loaded(base.filter(expertise=category))

    if chosen_profile is None:
        chosen_profile = least_loaded(base)

    if chosen_profile is None:
        dataset.assigned_reviewer = None
        dataset.save(update_fields=["assigned_reviewer"])
        return None

    dataset.assigned_reviewer = chosen_profile.user
    dataset.save(update_fields=["assigned_reviewer"])
    return chosen_profile.user
