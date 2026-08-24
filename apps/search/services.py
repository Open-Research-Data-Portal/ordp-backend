from django.db.models import Q, F, Count, Sum
from django.contrib.postgres.search import SearchQuery, SearchRank, SearchVector

from apps.datasets.models import Dataset, DatasetFile

ORDER_BY_MAP = {
    "newest": "-created_at",
    "title": "title",
    "popular": "-popularity",
    "downloads": "-download_count",
}


def visible_datasets_queryset():
    """Existence is discoverable regardless of visibility tier — public,
    institutional, AND restricted datasets all show up in search/discovery.
    Access to the actual files is gated separately, at download/share time."""
    return Dataset.objects.filter(is_active=True, status=Dataset.Status.PUBLISHED)


def apply_ordering(qs, order_by):
    if order_by == "popular":
        qs = qs.annotate(popularity=F("view_count") + F("download_count"))
    return qs.order_by(ORDER_BY_MAP.get(order_by, "-created_at"))


def build_dataset_search_queryset(*, query, user=None, category_id=None, order_by=None):
    base_qs = visible_datasets_queryset()

    if category_id:
        base_qs = base_qs.filter(metadata__category_id=category_id)

    if query:
        search_query = SearchQuery(query, config="english")
        search_vector = (
            SearchVector("title", config="english") +
            SearchVector("metadata__description", config="english") +
            SearchVector("metadata__sponsor_or_grant", config="english") +
            SearchVector("metadata__category__name", config="english") +
            SearchVector("metadata__subject__name", config="english") +
            SearchVector("metadata__keywords__word", config="english")
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


def _file_stats_by_dataset(datasets):
    """One query for file count + total size across all given datasets,
    instead of querying per-dataset (N+1)."""
    ids = [d.id for d in datasets]
    rows = (
        DatasetFile.objects.filter(dataset_id__in=ids)
        .values("dataset_id").annotate(file_count=Count("id"), total_size=Sum("file_size"))
    )
    return {row["dataset_id"]: row for row in rows}


def _serialize_feed_item(dataset, file_stats):
    stats = file_stats.get(dataset.id, {})
    return {
        "id": dataset.id, "title": dataset.title,
        "view_count": dataset.view_count, "download_count": dataset.download_count,
        "created_at": dataset.created_at, "thumbnail_key": dataset.thumbnail_key,
        "file_count": stats.get("file_count", 0), "total_size_bytes": stats.get("total_size") or 0,
    }


def build_discovery_feed(user, limit=20):
    profile = getattr(user, "profile", None)
    interest_category_ids = list(profile.expertise.values_list("id", flat=True)) if profile else []

    visible = visible_datasets_queryset().exclude(owner=user)
    trending = visible.annotate(popularity=F("view_count") + F("download_count")).order_by("-popularity", "-created_at")

    if not interest_category_ids:
        results = list(trending[:limit])
        file_stats = _file_stats_by_dataset(results)
        return {"feed_type": "discovery", "results": [_serialize_feed_item(d, file_stats) for d in results]}

    personalized = visible.filter(metadata__category_id__in=interest_category_ids).order_by("-created_at")
    personalized_count = personalized.count()

    if personalized_count < DISCOVERY_MIN_PERSONALIZED_RESULTS:
        seen_ids = set()
        blended = []
        for d in list(personalized[:limit]) + list(trending[:limit]):
            if d.id not in seen_ids:
                seen_ids.add(d.id)
                blended.append(d)
        results = blended[:limit]
        file_stats = _file_stats_by_dataset(results)
        return {"feed_type": "blended_fallback", "results": [_serialize_feed_item(d, file_stats) for d in results]}

    personalized_share = limit if len(interest_category_ids) >= 3 else max(1, limit // 2)
    personalized_slice = list(personalized[:personalized_share])
    discovery_fill = [d for d in trending[:limit] if d.id not in {p.id for p in personalized_slice}]
    combined = personalized_slice + discovery_fill[: limit - len(personalized_slice)]
    file_stats = _file_stats_by_dataset(combined)

    return {
        "feed_type": "personalized" if len(interest_category_ids) >= 3 else "partially_personalized",
        "results": [_serialize_feed_item(d, file_stats) for d in combined],
    }