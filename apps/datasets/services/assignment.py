import random
from django.db.models import Count, Q
from apps.accounts.models import UserProfile


def assign_reviewer(dataset):
    from .models import Dataset 

    category = None
    if hasattr(dataset, "metadata") and dataset.metadata.category_id:
        category = dataset.metadata.category

    def least_loaded(qs):
        candidates = list(
            qs.annotate(
                pending_count=Count(
                    "user__assigned_datasets",
                    filter=Q(user__assigned_datasets__status=Dataset.Status.PENDING),
                )
            )
        )
        if not candidates:
            return None
        min_load = min(c.pending_count for c in candidates)
        tied = [c for c in candidates if c.pending_count == min_load]
        return random.choice(tied)

    chosen_profile = None
    if category is not None:
        matching = UserProfile.objects.filter(role="checker", expertise=category)
        chosen_profile = least_loaded(matching)

    if chosen_profile is None:
        all_checkers = UserProfile.objects.filter(role="checker")
        chosen_profile = least_loaded(all_checkers)

    if chosen_profile is None:
        return None

    dataset.assigned_reviewer = chosen_profile.user
    dataset.save(update_fields=["assigned_reviewer"])
    return chosen_profile.user