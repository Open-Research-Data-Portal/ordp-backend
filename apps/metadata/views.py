from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from django.shortcuts import get_object_or_404

from apps.datasets.models import Dataset
from .models import Keyword, Metadata, Category, Subject, Language, DatasetCharacteristic
from .serializers import MetadataSerializer, CategorySerializer, SubjectSerializer
from .services import assign_fallback_thumbnail, get_or_create_pending_category, get_or_create_pending


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


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def list_languages(request):
    qs = Language.objects.filter(status=Language.Status.APPROVED).order_by("name")
    return Response([{"id": l.id, "name": l.name} for l in qs])


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def set_dataset_languages(request, dataset_id):
    dataset = get_object_or_404(Dataset, id=dataset_id, owner=request.user)
    language_ids = request.data.getlist("language_ids") if hasattr(request.data, "getlist") else request.data.get("language_ids", [])
    other_languages = request.data.getlist("other_languages") if hasattr(request.data, "getlist") else request.data.get("other_languages", [])

    languages = list(Language.objects.filter(id__in=language_ids))
    for name in other_languages:
        name = (name or "").strip()
        if name:
            languages.append(get_or_create_pending(Language, name, request.user))

    if not languages:
        return Response({"detail": "At least one language is required."}, status=400)

    dataset.languages.set(languages)
    return Response({"status": "languages set", "count": len(languages)})


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def list_characteristics(request):
    qs = DatasetCharacteristic.objects.filter(status=DatasetCharacteristic.Status.APPROVED).order_by("name")
    return Response([{"id": c.id, "name": c.name} for c in qs])


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def set_dataset_characteristics(request, dataset_id):
    dataset = get_object_or_404(Dataset, id=dataset_id, owner=request.user)
    characteristic_ids = request.data.getlist("characteristic_ids") if hasattr(request.data, "getlist") else request.data.get("characteristic_ids", [])
    other_characteristics = request.data.getlist("other_characteristics") if hasattr(request.data, "getlist") else request.data.get("other_characteristics", [])

    characteristics = list(DatasetCharacteristic.objects.filter(id__in=characteristic_ids))
    for name in other_characteristics:
        name = (name or "").strip()
        if name:
            characteristics.append(get_or_create_pending(DatasetCharacteristic, name, request.user))

    dataset.characteristics.set(characteristics)
    return Response({"status": "characteristics set", "count": len(characteristics)})