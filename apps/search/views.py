from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from apps.datasets.serializers import DatasetSerializer
from .services import build_dataset_search_queryset, build_discovery_feed, FILE_SIZE_MAP

VALID_ORDER_BY = ("newest", "title", "popular", "downloads")
VALID_VISIBILITIES = ("public", "institutional", "restricted")

VALID_ORDER_BY = ("newest", "title", "popular", "downloads")
VALID_FILE_SIZES = ("small", "medium", "large")
VALID_VISIBILITIES = ("public", "institutional", "restricted")

@api_view(["GET"])
@permission_classes([AllowAny])
def list_datasets(request):
    """Public — no login required. Existence of public/institutional/restricted
    datasets is all discoverable; actual file access is gated separately."""
    query = request.query_params.get("q", "").strip()
    category_id = request.query_params.get("category")
    order_by = request.query_params.get("order_by", "").strip()

    if order_by and order_by not in VALID_ORDER_BY:
        return Response({"detail": f"order_by must be one of: {', '.join(VALID_ORDER_BY)}."}, status=400)

    file_size = request.query_params.get("file_size", "").strip()

#     if file_size and file_size not in FILE_SIZE_MAP:
#         return Response({"detail": "file_size must be one of: small, medium, large."}, status=400)

    if file_size and file_size not in VALID_FILE_SIZES:
        return Response({"detail": f"file_size must be one of: {', '.join(VALID_FILE_SIZES)}."}, status=400)


    visibility = request.query_params.get("visibility", "").strip()
    if visibility and visibility not in VALID_VISIBILITIES:
        return Response({"detail": f"visibility must be one of: {', '.join(VALID_VISIBILITIES)}."}, status=400)

    extra_params = {
        "file_type":             request.query_params.get("file_type", "").strip(),
        "subject":               request.query_params.get("subject", "").strip(),
        "keyword":               request.query_params.get("keyword", "").strip(),
        "language":              request.query_params.get("language", "").strip(),
        "sponsor":               request.query_params.get("sponsor", "").strip(),
        "coverage":              request.query_params.get("coverage", "").strip(),
        "doi":                   request.query_params.get("doi", "").strip(),
        "owner":                 request.query_params.get("owner", "").strip(),
        "min_file_count":        request.query_params.get("min_file_count", "").strip(),
        "max_file_count":        request.query_params.get("max_file_count", "").strip(),
        "date_from":             request.query_params.get("date_from", "").strip(),
        "date_to":               request.query_params.get("date_to", "").strip(),
        "file_size":             file_size,
        "visibility":            visibility,
        "has_contributors":      request.query_params.get("has_contributors", "").strip(),
        "bookmarked":            request.query_params.get("bookmarked", "").strip(),
        "has_multiple_versions": request.query_params.get("has_multiple_versions", "").strip(),
        "download_min":          request.query_params.get("download_min", "").strip(),
        "download_max":          request.query_params.get("download_max", "").strip(),
    }


    qs = build_dataset_search_queryset(
        query=query,
        user=request.user if request.user.is_authenticated else None,
        category_id=category_id,
        order_by=order_by or None,
        extra_params=extra_params,
    )



#     qs = build_dataset_search_queryset(
#         query=query,
#         user=request.user,
#         category_id=category_id,
#         order_by=order_by or None,
#         extra_params=extra_params,
#     )

#     qs = build_dataset_search_queryset(query=query, category_id=category_id, order_by=order_by)


    return Response(DatasetSerializer(qs, many=True).data)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def discover(request):
    """Requires login — personalization is tied to the account's saved
    interests. A logged-in user with zero interests still gets a good feed
    (falls back to trending), they just don't get personalization."""
    return Response(build_discovery_feed(request.user))