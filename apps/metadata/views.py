from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from .services import assign_fallback_thumbnail, get_or_create_pending_category
from apps.datasets.models import Dataset
from .models import Keyword, Metadata, Category, Subject

from .serializers import MetadataSerializer, CategorySerializer, SubjectSerializer
from django.shortcuts import get_object_or_404

@api_view(["POST"])
@permission_classes([IsAuthenticated])
def attach_metadata(request, dataset_id):
    dataset = get_object_or_404(Dataset, id=dataset_id, owner=request.user)

    category_id = request.data.get("category_id")
    other_category = (request.data.get("other_category") or "").strip()
    if not category_id and not other_category:
        return Response({"detail": "category_id or other_category is required."}, status=400)
    category = (
        get_object_or_404(Category, id=category_id) if category_id
        else get_or_create_pending_category(other_category, request.user)
    )

    serializer = MetadataSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    validated = dict(serializer.validated_data)
    validated["category"] = category 
    keywords = validated.pop("keywords", None)

    metadata, _ = Metadata.objects.update_or_create(dataset=dataset, defaults=validated)
    if keywords is not None:
        keyword_objects = [
            Keyword.objects.get_or_create(word=word.strip())[0]
            for word in keywords
            if word and word.strip()
        ]
        metadata.keywords.set(keyword_objects)

    assign_fallback_thumbnail(dataset)
    return Response({"status": "metadata attached"}, status=200)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def list_categories(request):
    qs = Category.objects.filter(status=Category.Status.APPROVED).order_by("name")
    return Response([{"id": c.id, "name": c.name} for c in qs])


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def list_subjects(request):
    return Response(SubjectSerializer(Subject.objects.all(), many=True).data)
