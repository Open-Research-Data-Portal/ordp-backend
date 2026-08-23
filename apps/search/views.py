from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response

from apps.datasets.serializers import DatasetSerializer
from .services import build_dataset_search_queryset, build_discovery_feed


@api_view(["GET"])
@permission_classes([AllowAny])
def list_datasets(request):
    """Public — no login required. Existence of public/institutional/restricted
    datasets is all discoverable; actual file access is gated separately."""
    query = request.query_params.get("q", "").strip()
    category_id = request.query_params.get("category")
    order_by = request.query_params.get("order_by")

    if order_by and order_by not in ("newest", "title", "popular", "downloads"):
        return Response({"detail": "order_by must be one of: newest, title, popular, downloads."}, status=400)

    qs = build_dataset_search_queryset(query=query, category_id=category_id, order_by=order_by)
    return Response(DatasetSerializer(qs, many=True).data)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def discover(request):
    """Requires login — personalization is tied to the account's saved
    interests. A logged-in user with zero interests still gets a good feed
    (falls back to trending), they just don't get personalization."""
    return Response(build_discovery_feed(request.user))