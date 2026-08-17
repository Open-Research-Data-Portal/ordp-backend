from django.db.models import Q, F, Exists, OuterRef
from django.contrib.postgres.search import SearchQuery, SearchRank, SearchVector

from apps.datasets.models import Dataset, Bookmark, Contributor

ORDER_BY_MAP = {
    "newest": "-created_at",
    "title": "title",
    "popular": "-popularity",
    "downloads": "-download_count",
}

FILE_SIZE_MAP = {
    "small":  (0, 1 * 1024 * 1024),
    "medium": (1 * 1024 * 1024, 50 * 1024 * 1024),
    "large":  (50 * 1024 * 1024, None),
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


def apply_common_filters(qs, params, user):
    """Filters shared between list_datasets and my_datasets."""

    file_type = params.get("file_type", "").strip()
    if file_type:
        qs = qs.filter(files__file_type__iexact=file_type)

    date_from = params.get("date_from", "").strip()
    date_to = params.get("date_to", "").strip()
    if date_from:
        qs = qs.filter(created_at__date__gte=date_from)
    if date_to:
        qs = qs.filter(created_at__date__lte=date_to)

    file_size = params.get("file_size", "").strip()
    if file_size and file_size in FILE_SIZE_MAP:
        size_min, size_max = FILE_SIZE_MAP[file_size]
        if size_min:
            qs = qs.filter(files__file_size__gte=size_min)
        if size_max:
            qs = qs.filter(files__file_size__lt=size_max)

    has_contributors = params.get("has_contributors", "").strip()
    if has_contributors == "true":
        qs = qs.filter(Exists(Contributor.objects.filter(dataset=OuterRef("pk"))))
    elif has_contributors == "false":
        qs = qs.exclude(Exists(Contributor.objects.filter(dataset=OuterRef("pk"))))

    bookmarked = params.get("bookmarked", "").strip()
    if bookmarked == "true":
        qs = qs.filter(Exists(Bookmark.objects.filter(dataset=OuterRef("pk"), user=user)))

    has_multiple_versions = params.get("has_multiple_versions", "").strip()
    if has_multiple_versions == "true":
        qs = qs.filter(version__gt=1)
    elif has_multiple_versions == "false":
        qs = qs.filter(version=1)

    download_min = params.get("download_min", "").strip()
    download_max = params.get("download_max", "").strip()
    if download_min.isdigit():
        qs = qs.filter(download_count__gte=int(download_min))
    if download_max.isdigit():
        qs = qs.filter(download_count__lte=int(download_max))

    return qs


def build_dataset_search_queryset(*, query, user=None, category_id=None,
                                   order_by=None, extra_params=None):
    base_qs = visible_datasets_queryset(user)
    extra_params = extra_params or {}

    profile = getattr(user, "profile", None)
    visibility = extra_params.get("visibility", "").strip()
    if visibility and profile and profile.has_role("admin"):
        base_qs = base_qs.filter(visibility=visibility)

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

    base_qs = apply_common_filters(base_qs, extra_params, user)
    base_qs = base_qs.distinct()
    return apply_ordering(base_qs, order_by) if order_by else base_qs.order_by("-created_at")


DISCOVERY_MIN_PERSONALIZED_RESULTS = 5


def build_discovery_feed(user, limit=20):
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
                {"id": d.id, "title": d.title, "view_count": d.view_count,
                 "download_count": d.download_count, "created_at": d.created_at}
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
            {"id": d.id, "title": d.title, "view_count": d.view_count,
             "download_count": d.download_count, "created_at": d.created_at}
            for d in combined
        ],
    }