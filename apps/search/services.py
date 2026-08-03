from django.db.models import Q
from django.contrib.postgres.search import SearchQuery, SearchRank, SearchVector

from apps.datasets.models import Dataset


def build_dataset_search_queryset(*, query, user=None):
    base_qs = Dataset.objects.filter(is_active=True)
    profile = getattr(user, "profile", None) if user is not None else None
    if getattr(profile, "role", None) != "admin":
        base_qs = base_qs.filter(Q(visibility=Dataset.Visibility.PUBLIC) | Q(owner=user))

    search_query = SearchQuery(query)
    search_vector = (
        SearchVector("title") +
        SearchVector("metadata__description") +
        SearchVector("metadata__sponsor_or_grant") +
        SearchVector("metadata__category__name") +
        SearchVector("metadata__subject__name") +
        SearchVector("metadata__keywords__word")
    )

    return (
        base_qs.annotate(rank=SearchRank(search_vector, search_query))
        .filter(
            Q(title__icontains=query) |
            Q(metadata__description__icontains=query) |
            Q(metadata__sponsor_or_grant__icontains=query) |
            Q(metadata__category__name__icontains=query) |
            Q(metadata__subject__name__icontains=query) |
            Q(metadata__keywords__word__icontains=query) |
            Q(rank__gt=0)
        )
        .distinct()
        .order_by("-rank", "-created_at")
    )