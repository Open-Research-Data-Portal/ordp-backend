from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.datasets.models import Dataset
from .models import Keyword, Metadata, Category, Subject
from .serializers import MetadataSerializer, CategorySerializer, SubjectSerializer
from django.shortcuts import get_object_or_404

@api_view(["POST"])
@permission_classes([IsAuthenticated])
def attach_metadata(request, dataset_id):
    dataset = get_object_or_404(Dataset, id=dataset_id, owner=request.user)
    serializer = MetadataSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)

    validated = dict(serializer.validated_data)
    keywords = validated.pop("keywords", None)

    metadata, _ = Metadata.objects.update_or_create(dataset=dataset, defaults=validated)
    if keywords is not None:
        keyword_objects = [
            Keyword.objects.get_or_create(word=word.strip())[0]
            for word in keywords
            if word and word.strip()
        ]
        metadata.keywords.set(keyword_objects)

    from .services import assign_fallback_thumbnail
    assign_fallback_thumbnail(dataset)

    return Response({"status": "metadata attached"}, status=200)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def list_categories(request):
    return Response(CategorySerializer(Category.objects.all(), many=True).data)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def list_subjects(request):
    return Response(SubjectSerializer(Subject.objects.all(), many=True).data)