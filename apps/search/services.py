from django.db.models import Q, F
from django.contrib.postgres.search import SearchQuery, SearchRank, SearchVector

from apps.datasets.models import Dataset

ORDER_BY_MAP = {
    "newest": "-created_at",
    "title": "title",
    "popular": "-popularity",
    "downloads": "-download_count",
}


def visible_datasets_queryset(user):
    base_qs = Dataset.objects.filter(is_active=True, status=Dataset.Status.APPROVED)
    profile = getattr(user, "profile", None)
    if not (profile and profile.has_role("admin")):
        base_qs = base_qs.filter(Q(visibility=Dataset.Visibility.PUBLIC) | Q(owner=user))
    return base_qs


def apply_ordering(qs, order_by):
    if order_by == "popular":
        qs = qs.annotate(popularity=F("view_count") + F("download_count"))
    return qs.order_by(ORDER_BY_MAP.get(order_by, "-created_at"))


def build_dataset_search_queryset(*, query, user=None, category_id=None, order_by=None):
    base_qs = visible_datasets_queryset(user)

    if category_id:
        base_qs = base_qs.filter(metadata__category_id=category_id)

    if query:
        search_query = SearchQuery(query)
        search_vector = (
            SearchVector("title") +
            SearchVector("metadata__description") +
            SearchVector("metadata__sponsor_or_grant") +
            SearchVector("metadata__category__name") +
            SearchVector("metadata__subject__name") +
            SearchVector("metadata__keywords__word")
        )
        base_qs = (
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
        )

    base_qs = base_qs.distinct()
    return apply_ordering(base_qs, order_by) if order_by else base_qs.order_by("-created_at")


DISCOVERY_MIN_PERSONALIZED_RESULTS = 5


def build_discovery_feed(user, limit=20):
    """Feed-state logic per spec: 0 interests -> pure discovery (trending/recent);
    1-2 -> blended; 3+ -> personalization-led but still mixes in discovery content.
    Falls back gracefully whenever personalized results are too thin, rather than
    ever showing an empty feed."""
    profile = getattr(user, "profile", None)
    interest_category_ids = list(profile.expertise.values_list("id", flat=True)) if profile else []

    visible = visible_datasets_queryset(user).exclude(owner=user)
    trending = visible.annotate(popularity=F("view_count") + F("download_count")).order_by("-popularity", "-created_at")

    if not interest_category_ids:
        return {
            "feed_type": "discovery",
            "results": list(trending[:limit].values("id", "title", "view_count", "download_count", "created_at")),
        }

    personalized = visible.filter(metadata__category_id__in=interest_category_ids).order_by("-created_at")
    personalized_count = personalized.count()

    if personalized_count < DISCOVERY_MIN_PERSONALIZED_RESULTS:
        seen_ids = set()
        blended = []
        for d in list(personalized[:limit]) + list(trending[:limit]):
            if d.id not in seen_ids:
                seen_ids.add(d.id)
                blended.append(d)
        return {
            "feed_type": "blended_fallback",
            "results": [
                {"id": d.id, "title": d.title, "view_count": d.view_count, "download_count": d.download_count, "created_at": d.created_at}
                for d in blended[:limit]
            ],
        }

    personalized_share = limit if len(interest_category_ids) >= 3 else max(1, limit // 2)
    personalized_slice = list(personalized[:personalized_share])
    discovery_fill = [d for d in trending[:limit] if d.id not in {p.id for p in personalized_slice}]
    combined = personalized_slice + discovery_fill[: limit - len(personalized_slice)]

    return {
        "feed_type": "personalized" if len(interest_category_ids) >= 3 else "partially_personalized",
        "results": [
            {"id": d.id, "title": d.title, "view_count": d.view_count, "download_count": d.download_count, "created_at": d.created_at}
            for d in combined
        ],
    }