import os
import uuid
import math
import hashlib

from django.utils import timezone
from apps.accounts.models import ActivityLog
from apps.datasets.services.file_validation import FileTypeMismatchError
from .models import Bookmark, Contributor, DatasetWatcher, UploadSession
from django.shortcuts import get_object_or_404
from apps.accounts.permissions import CanUploadDatasets
from apps.notifications.services import notify
from apps.notifications.models import Notification
from .services.diffing import compute_diff
from .serializers import DatasetRevisionSerializer, PrepareUploadSerializer
from django.db import models as django_models
from django.conf import settings
from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes, parser_classes
from rest_framework.parsers import MultiPartParser
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.response import Response
from django.shortcuts import get_object_or_404
from .services.assignment import assign_reviewers


from apps.accounts.views import log_activity, get_client_ip
from .services.storage import (
    presigned_download_url,
    upload_fileobj,
    download_to_file,
)
import uuid as uuid_lib
from .permissions import IsDatasetOwner, IsDatasetOwnerOrContributor
from .services.revisions import route_change
from .models import DatasetRevision, PendingContentUpdate

from .models import Dataset, DatasetVersion
from .permissions import IsDatasetOwner
from .serializers import (
    DatasetSerializer,
    InitUploadSerializer,
    TermsAcceptanceSerializer,
    DatasetVersionSerializer,
)

from apps.metadata.serializers import MetadataSerializer
from .services.assembly import (
    finalize_upload,
    session_dir,
    running_total,
    UploadTooLargeError,
    MissingChunksError,
)


@api_view(["POST"])
@permission_classes([CanUploadDatasets])
def init_upload(request):
    """
    Step 1:
    Create the Dataset shell and open a chunked upload session.

    Visibility is optional during draft creation.
    It must be selected before final submission.
    """

    serializer = InitUploadSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)

    dataset = Dataset.objects.create(
    title=serializer.validated_data["title"],
    owner=request.user,
    visibility=serializer.validated_data.get("visibility"),
    embargo_end_date=serializer.validated_data.get("embargo_end_date"),
)

    upload_session_id = uuid.uuid4().hex

    UploadSession.objects.create(
        id=upload_session_id,
        dataset=dataset,
        uploader=request.user,
    )

    os.makedirs(
        session_dir(upload_session_id),
        exist_ok=True,
    )

    log_activity(
        user=request.user,
        action="dataset_upload_initiated",
        target_object=f"Dataset:{dataset.id}",
        ip_address=get_client_ip(request),
    )

    return Response(
        {
            "dataset_id": dataset.id,
            "upload_session_id": upload_session_id,
        },
        status=201,
    )

@api_view(["POST"])
@permission_classes([CanUploadDatasets])
def init_existing_draft_upload(request, dataset_id):
    """
    Resume an existing editable dataset upload.

    Reuses the latest unfinished upload session when available.
    Otherwise, creates a new upload session.
    """

    dataset = get_object_or_404(
        Dataset,
        id=dataset_id,
        owner=request.user,
        is_active=True,
    )

    # Only editable datasets can start/resume an upload.
    if dataset.status not in (
        Dataset.Status.DRAFT,
        Dataset.Status.CHANGES_REQUESTED,
    ):
        return Response(
            {
                "detail": (
                    "You can only resume uploads for draft datasets "
                    "or datasets with requested changes."
                )
            },
            status=400,
        )

    # Look for the latest unfinished upload session.
    session = (
        UploadSession.objects
        .filter(
            dataset=dataset,
            uploader=request.user,
            completed_at__isnull=True,
        )
        .order_by("-created_at")
        .first()
    )

    # No unfinished session exists, so create one.
    if session is None:
        upload_session_id = uuid.uuid4().hex

        session = UploadSession.objects.create(
            id=upload_session_id,
            dataset=dataset,
            uploader=request.user,
        )

        os.makedirs(
            session_dir(upload_session_id),
            exist_ok=True,
        )

        log_activity(
            user=request.user,
            action="dataset_upload_session_initiated",
            target_object=f"Dataset:{dataset.id}",
            ip_address=get_client_ip(request),
        )

        status_code = 201

    else:
        # Make sure the directory still exists.
        os.makedirs(
            session_dir(session.id),
            exist_ok=True,
        )

        status_code = 200

    return Response(
        {
            "dataset_id": str(dataset.id),
            "upload_session_id": session.id,
        },
        status=status_code,
    )


@api_view(["POST"])
@permission_classes([CanUploadDatasets])
def prepare_upload(request, upload_session_id):
    """
    Prepare a file upload after the user has selected a file.

    The dataset remains a draft. This endpoint only determines
    the chunk size and total number of chunks.
    """

    serializer = PrepareUploadSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)

    try:
        session = UploadSession.objects.select_related(
            "dataset",
            "uploader",
        ).get(
            id=upload_session_id,
            uploader=request.user,
        )
    except UploadSession.DoesNotExist:
        return Response(
            {"detail": "Unknown upload session."},
            status=404,
        )

    if session.completed_at is not None:
        return Response(
            {"detail": "This upload session has already been completed."},
            status=400,
        )

    filename = serializer.validated_data["filename"]
    file_size = serializer.validated_data["file_size"]
    file_checksum = serializer.validated_data["file_checksum"]

    if file_size > settings.MAX_DATASET_UPLOAD_SIZE:
        return Response(
            {"detail": "File exceeds maximum allowed size."},
            status=413,
        )

    chunk_size = calculate_chunk_size(file_size)
    total_chunks = math.ceil(file_size / chunk_size)

    session.original_filename = filename
    session.total_chunks = total_chunks
    session.file_checksum = file_checksum

    session.save(
        update_fields=[
            "original_filename",
            "total_chunks",
            "file_checksum",
            "updated_at",
        ]
    )

    return Response(
        {
            "upload_session_id": session.id,
            "chunk_size": chunk_size,
            "total_chunks": total_chunks,
        },
        status=200,
    )

@api_view(["POST"])
@permission_classes([CanUploadDatasets])
@parser_classes([MultiPartParser])
def upload_chunk(request, upload_session_id):
    """
    Step 2:
    Upload one chunk with checksum verification.

    Behavior:
    - Validates chunk_index.
    - Rejects indexes outside the expected range.
    - Verifies the received chunk checksum.
    - Detects identical duplicate chunks.
    - Rejects a duplicate index containing different data.
    """

    chunk_index = request.data.get("chunk_index")
    chunk_checksum = request.data.get("chunk_checksum")
    chunk_file = request.FILES.get("chunk")

    if chunk_file is None or chunk_index is None or not chunk_checksum:
        return Response(
            {
                "detail": (
                    "chunk, chunk_index, and chunk_checksum "
                    "are required."
                )
            },
            status=400,
        )

    try:
        chunk_index = int(chunk_index)
    except (TypeError, ValueError):
        return Response(
            {"detail": "chunk_index must be an integer."},
            status=400,
        )

    try:
        session = UploadSession.objects.select_related(
            "dataset",
            "uploader",
        ).get(
            id=upload_session_id,
            uploader=request.user,
        )
    except UploadSession.DoesNotExist:
        return Response(
            {"detail": "Unknown upload session."},
            status=404,
        )

    if session.completed_at is not None:
        return Response(
            {
                "detail": (
                    "This upload session has already been completed."
                )
            },
            status=400,
        )

    # ---------------------------------------------------------
    # 1. Validate chunk index
    # ---------------------------------------------------------

    if session.total_chunks is None:
        return Response(
            {
                "detail": (
                    "Upload must be prepared before chunks "
                    "can be uploaded."
                )
            },
            status=400,
        )

    if chunk_index < 0 or chunk_index >= session.total_chunks:
        return Response(
            {
                "detail": (
                    f"chunk_index must be between 0 and "
                    f"{session.total_chunks - 1}."
                )
            },
            status=400,
        )

    # ---------------------------------------------------------
    # 2. Calculate checksum of received chunk
    # ---------------------------------------------------------

    sha256 = hashlib.sha256()

    for part in chunk_file.chunks():
        sha256.update(part)

    received_checksum = sha256.hexdigest()

    # Reset the uploaded file so it can be read again below.
    chunk_file.seek(0)

    # Normalize the supplied checksum.
    chunk_checksum = str(chunk_checksum).strip().lower()

    if received_checksum != chunk_checksum:
        return Response(
            {
                "detail": "Chunk checksum mismatch.",
                "chunk_index": chunk_index,
            },
            status=400,
        )

    # ---------------------------------------------------------
    # 3. Determine chunk path
    # ---------------------------------------------------------

    d = session_dir(upload_session_id)
    os.makedirs(d, exist_ok=True)

    chunk_path = os.path.join(
        d,
        f"chunk_{chunk_index:06d}",
    )

    # ---------------------------------------------------------
    # 4. Duplicate detection
    # ---------------------------------------------------------

    if os.path.exists(chunk_path):
        existing_sha256 = hashlib.sha256()

        with open(chunk_path, "rb") as existing_chunk:
            while True:
                data = existing_chunk.read(1024 * 1024)

                if not data:
                    break

                existing_sha256.update(data)

        existing_checksum = existing_sha256.hexdigest()

        if existing_checksum == received_checksum:
            return Response(
                {
                    "status": "duplicate",
                    "chunk_index": chunk_index,
                },
                status=200,
            )

        return Response(
            {
                "detail": (
                    "A different chunk already exists at "
                    "this chunk_index."
                ),
                "chunk_index": chunk_index,
            },
            status=409,
        )

    # ---------------------------------------------------------
    # 5. Check total upload size
    # ---------------------------------------------------------

    if (
        running_total(upload_session_id) + chunk_file.size
        > settings.MAX_DATASET_UPLOAD_SIZE
    ):
        return Response(
            {"detail": "Upload exceeds maximum allowed size."},
            status=413,
        )

    # ---------------------------------------------------------
    # 6. Save chunk
    # ---------------------------------------------------------

    with open(chunk_path, "wb") as f:
        for part in chunk_file.chunks():
            f.write(part)

    return Response(
        {
            "status": "chunk received",
            "chunk_index": chunk_index,
            "checksum": received_checksum,
        },
        status=200,
    )

@api_view(["POST"])
@permission_classes([IsAuthenticated, IsDatasetOwner])
@parser_classes([MultiPartParser])
def upload_thumbnail(request, dataset_id):
    dataset = get_object_or_404(Dataset, id=dataset_id, owner=request.user)
    image = request.FILES.get("thumbnail")
    if image is None:
        return Response({"detail": "thumbnail file is required."}, status=400)
    key = f"thumbnails/{dataset.id}/{image.name}"
    upload_fileobj(image, key, getattr(image, "content_type", None))
    dataset.thumbnail_key = key
    dataset.thumbnail_source = Dataset.ThumbnailSource.UPLOADED
    dataset.save(update_fields=["thumbnail_key", "thumbnail_source"])
    return Response({"status": "thumbnail uploaded"}, status=200)
def calculate_chunk_size(file_size):
    MB = 1024 * 1024

    if file_size <= 10 * MB:
        return file_size

    if file_size <= 100 * MB:
        return 5 * MB

    if file_size <= 500 * MB:
        return 10 * MB

    return 20 * MB

@api_view(["POST"])
@permission_classes([CanUploadDatasets])
def complete_upload(request, upload_session_id):
    """
    Step 3:
    Assemble chunks, validate the file, push it to storage,
    and create DatasetFile.

    A failed validation or temporary storage failure does NOT
    destroy the upload session.
    """

    try:
        session = UploadSession.objects.select_related(
            "dataset",
            "uploader",
        ).get(
            id=upload_session_id,
            uploader=request.user,
        )
    except UploadSession.DoesNotExist:
        return Response(
            {"detail": "Upload session not found."},
            status=404,
        )

    if session.completed_at is not None:
        return Response(
            {"detail": "This upload session has already been completed."},
            status=400,
        )

    dataset_id = request.data.get("dataset_id")

    if str(dataset_id) != str(session.dataset_id):
        return Response(
            {"detail": "This upload session does not belong to this dataset."},
            status=403,
        )

    filename = request.data.get("filename")
    file_type = request.data.get("file_type")

    if not filename:
        return Response(
            {"detail": "filename is required."},
            status=400,
        )

    if not file_type:
        return Response(
            {"detail": "file_type is required."},
            status=400,
        )

    try:
        dataset_file = finalize_upload(
            dataset_id=session.dataset_id,
            upload_session_id=upload_session_id,
            uploader=request.user,
            original_filename=filename,
            declared_file_type=file_type,
            is_structured=request.data.get(
                "is_structured",
                True,
            ),
            column_count=request.data.get("column_count"),
            feature_names=request.data.get("feature_names"),
            item_count=request.data.get("item_count"),
        )

    except MissingChunksError as exc:
        return Response(
            {
                "detail": "Missing chunks.",
                "missing_chunks": exc.missing_indexes,
            },
            status=400,
        )

    except FileNotFoundError:
        return Response(
            {
                "detail": (
                    "Upload session not found or contains no uploaded chunks."
                )
            },
            status=404,
        )

    except PermissionError:
        return Response(
            {
                "detail": (
                    "You do not have permission to complete this upload."
                )
            },
            status=403,
        )

    except UploadTooLargeError:
        return Response(
            {"detail": "File exceeds size limit."},
            status=413,
        )

    except FileTypeMismatchError as exc:
        # IMPORTANT:
        # Session remains available.
        return Response(
            {"detail": str(exc)},
            status=400,
        )

    except Exception:
        import logging

        logging.exception("Upload finalize failed")

        # IMPORTANT:
        # Session remains available for retry.
        return Response(
            {"detail": "Upload failed. You can retry the completion step."},
            status=500,
        )

    # Mark the session completed ONLY after everything succeeded.
    session.completed_at = timezone.now()
    session.save(update_fields=["completed_at"])

    return Response(
        {
            "file_id": dataset_file.id,
            "checksum": dataset_file.checksum,
        },
        status=201,
    )


@api_view(["POST"])
@permission_classes([CanUploadDatasets])
def accept_terms_and_submit(request, dataset_id):
    """Step 4 (final step): accept dataset-level T&Cs, move draft/changes_requested -> pending.
    Also serves as the resubmit action after a reviewer requests changes."""
    dataset = get_object_or_404(Dataset, id=dataset_id, owner=request.user)
    if dataset.status not in (Dataset.Status.DRAFT, Dataset.Status.CHANGES_REQUESTED):
        return Response({"detail": f"Cannot submit a dataset with status '{dataset.status}'."}, status=400)
    if not hasattr(dataset, "metadata"):
        return Response({"detail": "Attach metadata before submitting."}, status=400)
    if not dataset.metadata.languages.exists():
        return Response({"detail": "At least one language is required before submitting."}, status=400)
    if not dataset.visibility:
        return Response(
            {"detail": "Select a visibility option before submitting the dataset."},
            status=400,
        )

    if dataset.embargo_end_date is not None:
        if dataset.visibility != Dataset.Visibility.PUBLIC:
            return Response(
                {
                    "detail": (
                        "An embargo can only be set for public datasets."
                    )
                },
                status=400,
            )

        if dataset.embargo_end_date <= timezone.now():
            return Response(
                {
                    "detail": (
                        "The embargo end date must be in the future."
                    )
                },
                status=400,
            )

    serializer = TermsAcceptanceSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    if not serializer.validated_data["terms_accepted"]:
        return Response({"detail": "You must accept the Terms & Conditions to submit a dataset."}, status=400)

    dataset.terms_accepted = True
    dataset.terms_accepted_at = timezone.now()
    dataset.terms_version = settings.CURRENT_TERMS_VERSION
    dataset.status = Dataset.Status.PENDING
    dataset.save(update_fields=["terms_accepted", "terms_accepted_at", "terms_version", "status"])
    assign_reviewers(dataset)
    log_activity(user=request.user, action="dataset_submitted",
                 target_object=f"Dataset:{dataset.id}", ip_address=get_client_ip(request))
    return Response({"status": "submitted for review"}, status=200)



@api_view(["GET"])
@permission_classes([IsAuthenticated])
def my_datasets(request):
    from apps.search.services import apply_common_filters, FILE_SIZE_MAP, apply_ordering

    qs = (
        Dataset.objects
        .filter(owner=request.user, is_active=True)
        .prefetch_related("files", "contributors")
        .distinct()
    )

    # Status filter — owners can see all their own statuses
    status = request.query_params.get("status", "").strip()
    if status and status in Dataset.Status.values:
        qs = qs.filter(status=status)

    # Visibility filter
    visibility = request.query_params.get("visibility", "").strip()
    if visibility and visibility in Dataset.Visibility.values:
        qs = qs.filter(visibility=visibility)

    # File size validation
    file_size = request.query_params.get("file_size", "").strip()
    if file_size and file_size not in FILE_SIZE_MAP:
        return Response({"detail": "file_size must be one of: small, medium, large."}, status=400)

    extra_params = {
        "file_type":             request.query_params.get("file_type", "").strip(),
        "date_from":             request.query_params.get("date_from", "").strip(),
        "date_to":               request.query_params.get("date_to", "").strip(),
        "file_size":             file_size,
        "has_contributors":      request.query_params.get("has_contributors", "").strip(),
        "bookmarked":            request.query_params.get("bookmarked", "").strip(),
        "has_multiple_versions": request.query_params.get("has_multiple_versions", "").strip(),
        "download_min":          request.query_params.get("download_min", "").strip(),
        "download_max":          request.query_params.get("download_max", "").strip(),
    }

    qs = apply_common_filters(qs, extra_params, request.user)

    order_by = request.query_params.get("order_by", "").strip()
    if order_by and order_by not in ("newest", "title", "popular", "downloads"):
        return Response({"detail": "order_by must be one of: newest, title, popular, downloads."}, status=400)

    qs = qs.distinct()
    return Response(DatasetSerializer(
        apply_ordering(qs, order_by) if order_by else qs.order_by("-created_at"),
        many=True
    ).data)

@api_view(["GET"])
@permission_classes([AllowAny])
def dataset_detail(request, dataset_id):
    dataset = get_object_or_404(Dataset, id=dataset_id, is_active=True)
    Dataset.objects.filter(id=dataset.id).update(view_count=django_models.F("view_count") + 1)
    dataset.refresh_from_db(fields=["view_count"])

    if request.user.is_authenticated:
        ActivityLog.objects.create(
            user=request.user, action="dataset_view", target_object=f"Dataset:{dataset.id}",
            ip_address=request.META.get("REMOTE_ADDR", "unknown"),
        )
    return Response(DatasetSerializer(dataset).data)

@api_view(["GET"])
@permission_classes([IsAuthenticated])
def dataset_versions(request, dataset_id):
    dataset = get_object_or_404(Dataset, id=dataset_id, is_active=True)
    versions = dataset.versions.select_related("changed_by", "changed_by__profile")
    return Response(DatasetVersionSerializer(versions, many=True).data)

def _download_to_tmp(file_key):
    local_path = f"/tmp/{uuid_lib.uuid4().hex}"
    download_to_file(file_key, local_path)
    return local_path


def _build_proposed_metadata(dataset, request_data):
    if not hasattr(dataset, "metadata"):
        return {}

    changed = {}
    current = dataset.metadata

    for field in SHARED_EDITABLE_METADATA_FIELDS:
        new_value = request_data.get(field)
        old_value = getattr(current, field, None)

        if new_value is not None and str(new_value) != str(old_value):
            changed[field] = {
                "old": old_value,
                "new": new_value,
            }

    return changed


OWNER_EDITABLE_FIELDS = (
    "title",
    "visibility",
    "embargo_end_date",
)
SHARED_EDITABLE_METADATA_FIELDS = (
    "description",
    "category_id",
    "sponsor_or_grant",
    "related_resources",
    "geographic_coverage",
    "temporal_coverage",
    "has_header",
    "has_missing_values",
    "instances_represent",
    "collection_method",
    "recommended_data_splits",
    "sensitive_data_disclosure",
    "data_preprocessing",
    "citation_notes",
)


@api_view(["PATCH"])
@permission_classes([IsAuthenticated, IsDatasetOwnerOrContributor])
def update_dataset(request, dataset_id):
    """Owner can change anything, including title/visibility. A linked
    researcher-contributor can only change descriptive metadata — not
    title/visibility, which are ownership-level decisions."""
    dataset = get_object_or_404(Dataset, id=dataset_id)
    is_owner = dataset.owner_id == request.user.id

    changed_fields = []

    if is_owner:
        new_visibility = request.data.get("visibility", dataset.visibility)
        new_embargo_end_date = request.data.get(
            "embargo_end_date",
            dataset.embargo_end_date,
        )

        if new_embargo_end_date is not None:
            embargo_serializer = InitUploadSerializer(
                data={
                    "title": dataset.title,
                    "visibility": new_visibility,
                    "embargo_end_date": new_embargo_end_date,
                }
            )
            embargo_serializer.is_valid(raise_exception=True)

            if new_visibility != Dataset.Visibility.PUBLIC:
                return Response(
                    {
                        "detail": "An embargo can only be set for public datasets."
                    },
                    status=400,
                )

            new_embargo_end_date = embargo_serializer.validated_data[
                "embargo_end_date"
            ]

        for field in OWNER_EDITABLE_FIELDS:
            if field in request.data:
                setattr(dataset, field, request.data[field])
                changed_fields.append(field)

        if new_visibility != Dataset.Visibility.PUBLIC:
            dataset.embargo_end_date = None
            if "embargo_end_date" not in changed_fields:
                changed_fields.append("embargo_end_date")

        dataset.save()

    if hasattr(dataset, "metadata"):
        metadata = dataset.metadata

        metadata_data = {
            field: request.data[field]
            for field in SHARED_EDITABLE_METADATA_FIELDS
            if field in request.data
        }

        if "category_id" in request.data:
            from apps.metadata.models import Category

            category_id = request.data["category_id"]

            category = get_object_or_404(
                Category,
                id=category_id,
                status=Category.Status.APPROVED,
            )

            metadata_data["category"] = category

    if metadata_data:
        metadata_serializer = MetadataSerializer(
            metadata,
            data=metadata_data,
            partial=True,
        )
        metadata_serializer.is_valid(raise_exception=True)
        metadata_serializer.save()

        changed_fields.extend(metadata_data.keys())

    if "language_ids" in request.data:
        from apps.metadata.models import Language

        if hasattr(dataset, "metadata"):
            dataset.metadata.languages.set(
                Language.objects.filter(
                    id__in=request.data["language_ids"],
                    status=Language.Status.APPROVED,
                )
            )
            changed_fields.append("languages")


    if "characteristic_ids" in request.data:
        from apps.metadata.models import DatasetCharacteristic

        if hasattr(dataset, "metadata"):
            dataset.metadata.characteristics.set(
                DatasetCharacteristic.objects.filter(
                    id__in=request.data["characteristic_ids"],
                    status=DatasetCharacteristic.Status.APPROVED,
                )
            )
            changed_fields.append("characteristics")
            changed_fields.append("characteristics")

    if changed_fields:
        log_activity(
            user=request.user, action="dataset_metadata_updated",
            target_object=f"Dataset:{dataset.id}", ip_address=get_client_ip(request),
            extra={"fields_changed": changed_fields},
        )

    if "upload_session_id" in request.data:
        current_file = dataset.files.latest("uploaded_at")
        try:
            new_dataset_file = finalize_upload(
                dataset_id=dataset_id, upload_session_id=request.data["upload_session_id"],
                uploader=request.user, original_filename=request.data.get("filename", "update"),
                declared_file_type=request.data.get("file_type", current_file.file_type),
            )
        except UploadTooLargeError:
            return Response({"detail": "File exceeds size limit."}, status=413)

        new_file_key = new_dataset_file.file_key
        old_local = _download_to_tmp(current_file.file_key)
        new_local = _download_to_tmp(new_file_key)
        diff_pct, summary = compute_diff(old_local, new_local, current_file.file_type)
        for p in (old_local, new_local):
            if os.path.exists(p):
                os.remove(p)

        source = (
            PendingContentUpdate.Source.OWNER_EDIT if dataset.owner_id == request.user.id
            else PendingContentUpdate.Source.CONTRIBUTOR_EDIT
        )
        result = route_change(
            dataset=dataset, source=source, submitted_by=request.user,
            new_file_key=new_file_key, diff_percentage=diff_pct, change_summary=summary,
            proposed_metadata=_build_proposed_metadata(dataset, request.data),
        )
        new_dataset_file.delete()
        return Response(result, status=202 if result["status"] == "pending_review" else 200)

    return Response({"status": "updated", "fields_changed": changed_fields}, status=200)

@api_view(["POST"])
@permission_classes([IsAuthenticated])
def request_revision_permission(request, dataset_id):
    dataset = get_object_or_404(Dataset, id=dataset_id, is_active=True)
    if dataset.is_owned_by(request.user) or Contributor.objects.filter(dataset=dataset, user=request.user).exists():
        return Response({"detail": "You already have edit access to this dataset."}, status=400)
    reason = (request.data.get("reason") or "").strip()
    if not reason:
        return Response({"detail": "Please describe why you want to propose a change."}, status=400)
    from .services.revisions import request_revision_permission as create_request
    req = create_request(dataset, request.user, reason)
    return Response({"status": "pending", "request_id": req.id}, status=201)


@api_view(["POST"])
@permission_classes([CanUploadDatasets])
def propose_revision(request, dataset_id):
    """Phase 2: actually submitting the change, only allowed once Phase 1
    (RevisionRequest) has been approved for this user+dataset, and the
    submitter's profile is complete."""
    from .services.revisions import has_revision_permission, consume_revision_permission

    dataset = get_object_or_404(Dataset, id=dataset_id)

    if not request.user.profile.is_profile_complete():
        return Response({"detail": "Please complete your profile before proposing a change."}, status=403)

    if not has_revision_permission(dataset, request.user):
        return Response({"detail": "You need reviewer-committee approval before proposing a change to this dataset."}, status=403)

    submitter_message = (request.data.get("submitter_message") or "").strip()
    if not submitter_message:
        return Response({"detail": "Please describe what you changed and why."}, status=400)

    current_file = dataset.files.latest("uploaded_at")
    try:
        new_dataset_file = finalize_upload(
            dataset_id=dataset_id, upload_session_id=request.data["upload_session_id"],
            uploader=request.user, original_filename=request.data["filename"],
            declared_file_type=request.data.get("file_type", current_file.file_type),
        )
    except UploadTooLargeError:
        return Response({"detail": "File exceeds size limit."}, status=413)

    new_file_key = new_dataset_file.file_key
    old_local = _download_to_tmp(current_file.file_key)
    new_local = _download_to_tmp(new_file_key)
    diff_pct, summary = compute_diff(old_local, new_local, current_file.file_type)
    for p in (old_local, new_local):
        if os.path.exists(p):
            os.remove(p)

    result = route_change(
        dataset=dataset, source=PendingContentUpdate.Source.REVISION, submitted_by=request.user,
        new_file_key=new_file_key, diff_percentage=diff_pct, change_summary=summary,
        proposed_metadata=_build_proposed_metadata(dataset, request.data),
    )
    new_dataset_file.delete()
    consume_revision_permission(dataset, request.user)
    return Response(result, status=202 if result["status"] == "pending_review" else 200)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def toggle_watch(request, dataset_id):
    dataset = get_object_or_404(Dataset, id=dataset_id, is_active=True)
    watcher, created = DatasetWatcher.objects.get_or_create(dataset=dataset, user=request.user)
    if not created:
        watcher.delete()
        return Response({"watching": False})
    return Response({"watching": True}, status=201)

@api_view(["GET"])
@permission_classes([IsAuthenticated])
def content_update_comparison(request, update_id):
    update = get_object_or_404(PendingContentUpdate.objects.select_related("dataset", "submitted_by"), id=update_id)
    profile = getattr(request.user, "profile", None)
    is_reviewer = bool(profile and profile.has_role("reviewer", "admin"))
    if not (update.dataset.is_owned_by(request.user) or is_reviewer):
        return Response({"detail": "You don't have permission to view this."}, status=403)

    current_file = update.dataset.files.latest("uploaded_at")
    return Response({
        "dataset_title": update.dataset.title,
        "submitted_by": update.submitted_by.profile.full_name if update.submitted_by else None,
        "submitted_at": update.created_at,
        "source": update.source,
        "ai_change_summary": update.change_summary,
        "diff_percentage": update.diff_percentage,
        "previous_download_url": presigned_download_url(current_file.file_key),
        "new_download_url": presigned_download_url(update.new_file_key),
        "metadata_diff": update.proposed_metadata,
        "status": update.status,
        "approve_votes": update.votes.filter(vote="approve").count(),
        "reject_votes": update.votes.filter(vote="reject").count(),
    })


@api_view(["POST"])
@permission_classes([IsAuthenticated, IsDatasetOwner])
def decide_revision(request, revision_id):
    revision = get_object_or_404(DatasetRevision, id=revision_id)
    decision = request.data.get("decision")

    if decision == "reject":
        reason = (request.data.get("reason") or "").strip()
        if not reason:
            return Response({"detail": "A reason is required to reject a revision."}, status=400)
        revision.status = DatasetRevision.Status.REJECTED
        revision.save()
        notify(
            user=revision.submitted_by, notification_type=Notification.NotificationType.REVISION_REJECTED,
            message=f'Your proposed revision to "{revision.dataset.title}" was declined: {reason}',
            dataset=revision.dataset, reason=reason,
        )
        return Response({"status": "rejected"})

    result = apply_revision(revision)
    return Response(result, status=200)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def toggle_bookmark(request, dataset_id):
    """Toggle: bookmarks it if not already bookmarked, removes it if it is."""
    dataset = get_object_or_404(Dataset, id=dataset_id, is_active=True)
    bookmark, created = Bookmark.objects.get_or_create(user=request.user, dataset=dataset)
    if not created:
        bookmark.delete()
        return Response({"bookmarked": False}, status=200)
    return Response({"bookmarked": True}, status=201)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def my_bookmarks(request):
    qs = (
        Dataset.objects
        .filter(bookmarked_by__user=request.user, is_active=True)
        .prefetch_related("files", "contributors")
        .order_by("-bookmarked_by__created_at")
    )
    return Response(DatasetSerializer(qs, many=True).data)

@api_view(["DELETE"])
@permission_classes([IsAuthenticated, IsDatasetOwner])
def soft_delete_dataset(request, dataset_id):
    dataset = get_object_or_404(Dataset, id=dataset_id, is_active=True)
    dataset.is_active = False
    dataset.save(update_fields=["is_active"])
    log_activity(
        user=request.user, action="dataset_soft_deleted",
        target_object=f"Dataset:{dataset.id}", ip_address=get_client_ip(request),
    )
    return Response(status=204)

@api_view(["PATCH"])
@permission_classes([IsAuthenticated])
def update_contributor_type(request, dataset_id, contributor_id):
    dataset = get_object_or_404(Dataset, id=dataset_id)
    if dataset.owner_id != request.user.id:
        return Response({"detail": "Only the original dataset owner can change contributor roles."}, status=403)

    contributor = get_object_or_404(Contributor, id=contributor_id, dataset=dataset)
    new_type = request.data.get("contributor_type")
    if new_type not in Contributor.ContributorType.values:
        return Response({"detail": "contributor_type must be 'owner', 'co_author', or 'contributor'."}, status=400)

    contributor.contributor_type = new_type
    contributor.save(update_fields=["contributor_type"])
    return Response({"status": "updated", "contributor_type": contributor.contributor_type})


@api_view(["DELETE"])
@permission_classes([IsAuthenticated])
def remove_contributor(request, dataset_id, contributor_id):
    dataset = get_object_or_404(Dataset, id=dataset_id)
    if dataset.owner_id != request.user.id:
        return Response({"detail": "Only the original dataset owner can remove contributors."}, status=403)

    contributor = get_object_or_404(Contributor, id=contributor_id, dataset=dataset)
    contributor.delete()
    return Response(status=204)

