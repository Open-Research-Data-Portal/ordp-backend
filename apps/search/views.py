from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.datasets.serializers import DatasetSerializer
from .services import build_dataset_search_queryset, build_discovery_feed


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def list_datasets(request):
    query = request.query_params.get("q", "").strip()
    category_id = request.query_params.get("category")
    order_by = request.query_params.get("order_by")

    if order_by and order_by not in ("newest", "title", "popular", "downloads"):
        return Response({"detail": "order_by must be one of: newest, title, popular, downloads."}, status=400)

    qs = build_dataset_search_queryset(query=query, user=request.user, category_id=category_id, order_by=order_by)
    return Response(DatasetSerializer(qs, many=True).data)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def discover(request):
    """Available to ANY authenticated user, not just researchers — discovery
    should work for public-role users too, matching the spec's 'Explore' feed."""
    return Response(build_discovery_feed(request.user))