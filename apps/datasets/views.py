import os
import uuid

from apps.accounts.models import ActivityLog
from apps.datasets.services.file_validation import FileTypeMismatchError
from .models import Bookmark, Contributor, DatasetWatcher
from django.shortcuts import get_object_or_404
from apps.accounts.permissions import IsProfileComplete, IsResearcherOnly
from apps.notifications.services import notify
from apps.notifications.models import Notification
from .services.diffing import compute_diff
from .serializers import DatasetRevisionSerializer
from rest_framework.permissions import AllowAny
from django.db import models as django_models
from django.conf import settings
from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes, parser_classes
from rest_framework.parsers import MultiPartParser
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.response import Response
from django.shortcuts import get_object_or_404
from .services.assignment import assign_reviewer

from apps.accounts.permissions import IsResearcherOrAdmin
from apps.accounts.views import log_activity, get_client_ip
from .services.storage import presigned_download_url, minio_client
import uuid as uuid_lib
from .permissions import IsDatasetOwner, IsDatasetOwnerOrContributor
from .services.revisions import route_change
from .models import DatasetRevision, PendingContentUpdate

from .models import Dataset, DatasetVersion
from .permissions import IsDatasetOwner
from .serializers import DatasetSerializer, InitUploadSerializer, TermsAcceptanceSerializer, DatasetVersionSerializer
from .services.assembly import finalize_upload, session_dir, running_total, UploadTooLargeError


@api_view(["POST"])
@permission_classes([IsResearcherOnly, IsProfileComplete])
def init_upload(request):
    """Step 1: create the Dataset shell (status=draft), open a chunked-upload session.
    The uploader IS the author/owner via dataset.owner — no separate Contributor row needed."""
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
@permission_classes([IsAuthenticated, IsResearcherOnly])
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
@permission_classes([IsAuthenticated, IsDatasetOwner])
@parser_classes([MultiPartParser])
def upload_thumbnail(request, dataset_id):
    dataset = get_object_or_404(Dataset, id=dataset_id, owner=request.user)
    image = request.FILES.get("thumbnail")
    if image is None:
        return Response({"detail": "thumbnail file is required."}, status=400)
    key = f"thumbnails/{dataset.id}/{image.name}"
    minio_client().put_object(settings.MINIO_BUCKET, key, image, image.size)
    dataset.thumbnail_key = key
    dataset.thumbnail_source = Dataset.ThumbnailSource.UPLOADED
    dataset.save(update_fields=["thumbnail_key", "thumbnail_source"])
    return Response({"status": "thumbnail uploaded"}, status=200)

@api_view(["POST"])
@permission_classes([IsAuthenticated, IsResearcherOnly])
def complete_upload(request, upload_session_id):
    """Step 3: assemble chunks, verify size + declared-type match, checksum,
    push to MinIO, create DatasetFile."""
    try:
        dataset_file = finalize_upload(
            dataset_id=request.data["dataset_id"], upload_session_id=upload_session_id,
            uploader=request.user, original_filename=request.data["filename"],
            declared_file_type=request.data["file_type"],
            is_structured=request.data.get("is_structured", True),
            column_count=request.data.get("column_count"),
            feature_names=request.data.get("feature_names"),
            item_count=request.data.get("item_count"),
        )
    except UploadTooLargeError:
        return Response({"detail": "File exceeds size limit."}, status=413)
    except FileTypeMismatchError as exc:
        return Response({"detail": str(exc)}, status=400)
    except Exception:
        import logging
        logging.exception("Upload finalize failed")
        return Response({"detail": "Upload failed."}, status=500)

    return Response({"file_id": dataset_file.id, "checksum": dataset_file.checksum}, status=201)


@api_view(["POST"])
@permission_classes([IsAuthenticated, IsResearcherOnly])
def accept_terms_and_submit(request, dataset_id):
    """Step 4 (final step): accept dataset-level T&Cs, move draft/changes_requested -> pending.
    Also serves as the resubmit action after a reviewer requests changes."""
    dataset = get_object_or_404(Dataset, id=dataset_id, owner=request.user)
    if dataset.status not in (Dataset.Status.DRAFT, Dataset.Status.CHANGES_REQUESTED):
        return Response({"detail": f"Cannot submit a dataset with status '{dataset.status}'."}, status=400)
    if not hasattr(dataset, "metadata"):
        return Response({"detail": "Attach metadata before submitting."}, status=400)
    if not dataset.languages.exists():
        return Response({"detail": "At least one language is required before submitting."}, status=400)

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
@api_view(["GET"])
@permission_classes([IsAuthenticated])
def my_datasets(request):
    from apps.search.services import apply_common_filters, FILE_SIZE_MAP, apply_ordering

    qs = (
        Dataset.objects
        .filter(owner=request.user, is_active=True)
        .prefetch_related("files", "contributors")
        .exclude(status=Dataset.Status.DRAFT, files__isnull=True)
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
    )

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
    minio_client.fget_object(settings.MINIO_BUCKET, file_key, local_path)
    return local_path


def _build_proposed_metadata(dataset, request_data):
    if not hasattr(dataset, "metadata"):
        return {}
    changed = {}
    current = dataset.metadata
    for field in ("description", "category_id", "subject_id", "sponsor_or_grant"):
        new_value = request_data.get(field)
        old_value = getattr(current, field, None)
        if new_value is not None and str(new_value) != str(old_value):
            changed[field] = {"old": old_value, "new": new_value}
    return changed


OWNER_EDITABLE_FIELDS = ("title", "visibility")
SHARED_EDITABLE_METADATA_FIELDS = ("description", "category_id", "subject_id", "sponsor_or_grant")


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
        for field in OWNER_EDITABLE_FIELDS:
            if field in request.data:
                setattr(dataset, field, request.data[field])
                changed_fields.append(field)
    dataset.save()

    if hasattr(dataset, "metadata"):
        metadata_changed = []
        metadata = dataset.metadata
        for field in SHARED_EDITABLE_METADATA_FIELDS:
            if field in request.data:
                setattr(metadata, field, request.data[field])
                metadata_changed.append(field)
        if metadata_changed:
            metadata.save(update_fields=metadata_changed)
            changed_fields.extend(metadata_changed)

    if "language_ids" in request.data:
        from apps.metadata.models import Language
        dataset.languages.set(Language.objects.filter(id__in=request.data["language_ids"]))
        changed_fields.append("languages")

    if "characteristic_ids" in request.data:
        from apps.metadata.models import DatasetCharacteristic
        dataset.characteristics.set(DatasetCharacteristic.objects.filter(id__in=request.data["characteristic_ids"]))
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
@permission_classes([IsAuthenticated, IsResearcherOnly])
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
    is_reviewer = bool(profile and profile.has_role("checker", "admin"))
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