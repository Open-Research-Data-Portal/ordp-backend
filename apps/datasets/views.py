import os
import uuid

from django.conf import settings
from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes, parser_classes
from rest_framework.parsers import MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from django.shortcuts import get_object_or_404
from .services.assignment import assign_reviewer

from apps.accounts.permissions import IsResearcherOrAdmin
from apps.accounts.views import log_activity, get_client_ip

from .models import Dataset
from .permissions import IsDatasetOwner
from .serializers import DatasetSerializer, InitUploadSerializer, TermsAcceptanceSerializer
from .services.assembly import finalize_upload, session_dir, running_total, UploadTooLargeError


@api_view(["POST"])
@permission_classes([IsAuthenticated, IsResearcherOrAdmin])
def init_upload(request):
    """Step 1: create the Dataset shell (status=draft), open a chunked-upload session."""
    serializer = InitUploadSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)

    dataset = Dataset.objects.create(
        title=serializer.validated_data["title"], owner=request.user,
        visibility=serializer.validated_data["visibility"],
    )
    upload_session_id = uuid.uuid4().hex
    os.makedirs(session_dir(upload_session_id), exist_ok=True)

    log_activity(user=request.user, action="dataset_upload_initiated",
                 target_object=f"Dataset:{dataset.id}", ip_address=get_client_ip(request))
    return Response({"dataset_id": dataset.id, "upload_session_id": upload_session_id}, status=201)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
@parser_classes([MultiPartParser])
def upload_chunk(request, upload_session_id):
    """Step 2: upload one chunk. Rejects early (413) once the running total exceeds
    MAX_DATASET_UPLOAD_SIZE, without waiting for the final chunk."""
    chunk_index = request.data.get("chunk_index")
    chunk_file = request.FILES.get("chunk")
    if chunk_file is None or chunk_index is None:
        return Response({"detail": "chunk and chunk_index are required."}, status=400)

    d = session_dir(upload_session_id)
    if not os.path.isdir(d):
        return Response({"detail": "Unknown upload session."}, status=404)

    if running_total(upload_session_id) + chunk_file.size > settings.MAX_DATASET_UPLOAD_SIZE:
        return Response({"detail": "Upload exceeds maximum allowed size."}, status=413)

    chunk_path = os.path.join(d, f"chunk_{int(chunk_index):06d}")
    with open(chunk_path, "wb") as f:
        for part in chunk_file.chunks():
            f.write(part)
    return Response({"status": "chunk received"}, status=200)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def complete_upload(request, upload_session_id):
    """Step 3: assemble chunks, verify size, checksum, push to MinIO, create DatasetFile."""
    try:
        dataset_file = finalize_upload(
            dataset_id=request.data["dataset_id"], upload_session_id=upload_session_id,
            uploader=request.user, original_filename=request.data["filename"],
            declared_file_type=request.data["file_type"],
        )
    except UploadTooLargeError:
        return Response({"detail": "File exceeds size limit."}, status=413)
    except Exception:
        return Response({"detail": "Upload failed."}, status=500)

    return Response({"file_id": dataset_file.id, "checksum": dataset_file.checksum}, status=201)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def accept_terms_and_submit(request, dataset_id):
    """Step 4 (final step): accept dataset-level T&Cs, move status draft -> pending."""
    dataset = get_object_or_404(Dataset, id=dataset_id, owner=request.user)
    if not hasattr(dataset, "metadata"):
        return Response({"detail": "Attach metadata before submitting."}, status=400)

    serializer = TermsAcceptanceSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    if not serializer.validated_data["terms_accepted"]:
        return Response({"detail": "You must accept the Terms & Conditions to submit a dataset."}, status=400)

    dataset.terms_accepted = True
    dataset.terms_accepted_at = timezone.now()
    dataset.terms_version = settings.CURRENT_TERMS_VERSION
    dataset.status = Dataset.Status.PENDING
    dataset.save(update_fields=["terms_accepted", "terms_accepted_at", "terms_version", "status"])
    assign_reviewer(dataset)
    log_activity(user=request.user, action="dataset_submitted",
                 target_object=f"Dataset:{dataset.id}", ip_address=get_client_ip(request))
    return Response({"status": "submitted for review"}, status=200)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def my_datasets(request):
    qs = Dataset.objects.filter(owner=request.user, is_active=True).order_by("-created_at")
    return Response(DatasetSerializer(qs, many=True).data)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def dataset_detail(request, dataset_id):
    dataset = get_object_or_404(Dataset, id=dataset_id, is_active=True)
    return Response(DatasetSerializer(dataset).data)


# @api_view(["DELETE"])
# @permission_classes([IsAuthenticated, IsDatasetOwner])
# def soft_delete_dataset(request, dataset_id):
#     dataset = Dataset.objects.get(id=dataset_id, owner=request.user)
#     dataset.is_active = False
#     dataset.save(update_fields=["is_active"])
#     return Response(status=204)