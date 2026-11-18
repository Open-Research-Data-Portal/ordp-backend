"""HTTP endpoints for V2 proposals and immutable release history."""
import os
import uuid

from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.accounts.permissions import IsReviewerOrAdmin
from apps.metadata.models import Category
from apps.datasets.models import (
    Dataset, DatasetProposal, DatasetRelease,
    ResourceIdentifier, StoredObject, UploadSession, DatasetFile,
    Contributor, ChangeType, DatasetProposalActivity,
)
from apps.datasets.permissions import can_access_private_dataset
from apps.datasets.services.proposals import (
    ProposalLifecycleError, UnauthorizedProposalReviewerError,
    submit_proposal, submit_proposal_vote, cancel_proposal,
    request_proposal_changes, resubmit_requested_proposal, update_draft_proposal,
)
from apps.datasets.services.publication import (
    PublicationError, create_dataset_proposal, publish_proposal,
    add_proposal_file, replace_proposal_file, remove_proposal_file,
)
from apps.datasets.services.assembly import finalize_upload, session_dir, UploadTooLargeError, MissingChunksError


def _parse_uuid(value):
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        return None


def _can_submit_proposal(user, dataset):
    if dataset.is_owned_by(user):
        return True, DatasetProposal.Source.OWNER_EDIT
    contributor = Contributor.objects.filter(dataset=dataset, user=user).first()
    if contributor and contributor.can_edit():
        return True, DatasetProposal.Source.CONTRIBUTOR_EDIT
    from apps.datasets.services.revisions import has_revision_permission
    if has_revision_permission(dataset, user):
        return True, DatasetProposal.Source.REVISION_REQUEST
    return False, None


def _can_edit_proposal(user, proposal):
    if proposal.submitted_by_id != user.pk:
        return False
    allowed, _ = _can_submit_proposal(user, proposal.dataset)
    if allowed:
        return True
    return (
        proposal.source == DatasetProposal.Source.REVISION_REQUEST
        and proposal.activity.filter(action__in=[
            DatasetProposalActivity.Action.SUBMITTED,
            DatasetProposalActivity.Action.RESUBMITTED,
        ]).exists()
    )


def _can_read_dataset(user, dataset):
    if dataset.visibility == Dataset.Visibility.PRIVATE:
        return can_access_private_dataset(user, dataset)
    from apps.sharing.services import user_can_access_dataset
    return user_can_access_dataset(user, dataset)


def _release_payload(release, *, include_snapshot=True):
    files = []
    for entry in release.files.select_related("stored_object").order_by("display_order", "id"):
        obj = entry.stored_object
        files.append({
            "logical_file_id": str(entry.logical_file_id),
            "filename": obj.original_filename,
            "file_type": obj.file_type,
            "file_size": obj.file_size,
            "checksum_sha256": obj.checksum_sha256,
            "display_order": entry.display_order,
        })
    identifiers = list(
        ResourceIdentifier.objects.filter(release=release).order_by("role", "created_at")
        .values("scheme", "role", "identifier_value", "provider", "lifecycle_status",
                "registration_status", "landing_page_url", "registered_at")
    )
    attributions = list(
        release.attributions.order_by("attributed_at", "id").values(
            "name", "affiliation", "contribution_description", "attributed_at"
        )
    )
    from django.db.models import Q
    from apps.datasets.models import ResourceRelationship
    relationships = list(
        ResourceRelationship.objects.filter(
            Q(subject_identifier__release=release) | Q(related_identifier__release=release)
        ).select_related("subject_identifier", "related_identifier").order_by("created_at", "id")
    )
    relationship_payload = [{
        "relationship_type": item.relationship_type,
        "subject": {
            "role": item.subject_identifier.role,
            "identifier_value": item.subject_identifier.identifier_value,
            "registration_status": item.subject_identifier.registration_status,
        },
        "related": {
            "role": item.related_identifier.role,
            "identifier_value": item.related_identifier.identifier_value,
            "registration_status": item.related_identifier.registration_status,
        },
    } for item in relationships]
    payload = {
        "id": str(release.id),
        "dataset_id": str(release.dataset_id),
        "version": release.version,
        "status": release.status,
        "change_type": release.change_type,
        "change_summary": release.change_summary,
        "release_notes": release.release_notes,
        "base_release_id": str(release.base_release_id) if release.base_release_id else None,
        "published_at": release.published_at,
        "withdrawn_at": release.withdrawn_at,
        "withdrawal_reason": release.withdrawal_reason,
        "persistent_identifier": release.persistent_identifier,
        "citation_snapshot": release.citation_snapshot,
        "files": files,
        "identifiers": identifiers,
        "identifier_relationships": relationship_payload,
        "attributions": attributions,
    }
    if include_snapshot:
        payload["metadata_snapshot"] = release.metadata_snapshot
        payload["license_snapshot"] = release.license_snapshot
        payload["snapshot_schema_version"] = release.snapshot_schema_version
    return payload


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def create_proposal_view(request, dataset_id):
    dataset = get_object_or_404(Dataset, pk=dataset_id, is_active=True)
    can_submit, source = _can_submit_proposal(request.user, dataset)
    if not can_submit:
        return Response({"detail": "You do not have permission to create a proposal for this dataset."}, status=403)
    if not dataset.published_release_id:
        return Response({"detail": "The dataset does not have a V2 published release yet."}, status=409)
    proposed_metadata = request.data.get("proposed_metadata", {})
    if not isinstance(proposed_metadata, dict):
        return Response({"detail": "proposed_metadata must be an object."}, status=400)
    declared_change_type = request.data.get("declared_change_type")
    if declared_change_type is not None and declared_change_type not in ChangeType.values:
        return Response({"detail": "declared_change_type must be patch, minor, or major."}, status=400)
    change_summary = request.data.get("change_summary", "")
    release_notes = request.data.get("release_notes", "")
    if not isinstance(change_summary, str) or not isinstance(release_notes, str):
        return Response({"detail": "change_summary and release_notes must be strings."}, status=400)
    proposed_metadata = dict(proposed_metadata)
    if "category" in proposed_metadata:
        category_data = proposed_metadata["category"]
        if not isinstance(category_data, dict) or not category_data.get("id"):
            return Response({"detail": "category must include an approved category id."}, status=400)
        category_id = _parse_uuid(category_data["id"])
        if category_id is None:
            return Response({"detail": "category.id must be a valid UUID."}, status=400)
        category = get_object_or_404(
            Category, pk=category_id, status=Category.Status.APPROVED
        )
        proposed_metadata["category"] = {
            "id": str(category.pk), "name": category.name,
            "description": category.description or "",
        }
    try:
        proposal = create_dataset_proposal(
            dataset=dataset,
            base_release=dataset.published_release,
            submitted_by=request.user,
            source=source,
            proposed_metadata=proposed_metadata,
            declared_change_type=declared_change_type,
            change_summary=change_summary.strip(),
            release_notes=release_notes.strip(),
        )
    except (PublicationError, ValueError) as exc:
        return Response({"detail": str(exc)}, status=400)
    return Response({
        "id": str(proposal.id),
        "dataset_id": str(dataset.id),
        "base_release_id": str(proposal.base_release_id),
        "status": proposal.status,
        "proposed_metadata": proposal.proposed_metadata,
        "change_summary": proposal.change_summary,
        "files": proposal.files.count(),
    }, status=201)


def _proposal_payload(proposal):
    files = []
    for item in proposal.files.select_related("stored_object").order_by("display_order", "id"):
        obj = item.stored_object
        files.append({
            "logical_file_id": str(item.logical_file_id),
            "filename": obj.original_filename,
            "file_type": obj.file_type,
            "file_size": obj.file_size,
            "checksum_sha256": obj.checksum_sha256,
            "display_order": item.display_order,
        })
    votes = [{
        "reviewer_id": vote.reviewer_id,
        "reviewer_name": getattr(getattr(vote.reviewer, "profile", None), "full_name", ""),
        "vote": vote.vote,
        "reason": vote.reason,
        "voted_at": vote.voted_at,
    } for vote in proposal.votes.select_related("reviewer", "reviewer__profile").order_by("voted_at")]
    activity = [{
        "id": str(event.pk),
        "action": event.action,
        "actor_id": event.actor_id,
        "actor_name": getattr(getattr(event.actor, "profile", None), "full_name", "") if event.actor_id else None,
        "from_status": event.from_status,
        "to_status": event.to_status,
        "reason": event.reason,
        "details": event.details,
        "created_at": event.created_at,
    } for event in proposal.activity.select_related("actor", "actor__profile").all()]
    return {
        "id": str(proposal.pk),
        "dataset_id": str(proposal.dataset_id),
        "base_release_id": str(proposal.base_release_id),
        "status": proposal.status,
        "source": proposal.source,
        "proposed_metadata": proposal.proposed_metadata,
        "change_summary": proposal.change_summary,
        "release_notes": proposal.release_notes,
        "declared_change_type": proposal.declared_change_type,
        "computed_min_change_type": proposal.computed_min_change_type,
        "final_change_type": proposal.final_change_type,
        "submitted_by_id": proposal.submitted_by_id,
        "created_at": proposal.created_at,
        "submitted_at": proposal.submitted_at,
        "decided_at": proposal.decided_at,
        "published_at": proposal.published_at,
        "resulting_release_id": str(proposal.resulting_release_id) if proposal.resulting_release_id else None,
        "review_bypassed_at": proposal.review_bypassed_at,
        "review_bypassed_by_id": proposal.review_bypassed_by_id,
        "review_bypass_reason": proposal.review_bypass_reason,
        "files": files,
        "votes": votes,
        "activity": activity,
    }


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def dataset_proposals_view(request, dataset_id):
    dataset = get_object_or_404(Dataset, pk=dataset_id, is_active=True)
    profile = getattr(request.user, "profile", None)
    is_reviewer_or_admin = bool(profile and profile.has_role("reviewer", "admin"))
    is_submitter = dataset.proposals.filter(submitted_by=request.user).exists()
    if not dataset.is_owned_by(request.user) and not is_reviewer_or_admin and not is_submitter:
        return Response({"detail": "You cannot view proposals for this dataset."}, status=403)
    proposals = dataset.proposals.select_related("base_release", "submitted_by").prefetch_related("files", "votes", "activity__actor__profile")
    return Response([_proposal_payload(item) for item in proposals])


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def proposal_detail_view(request, proposal_id):
    proposal = get_object_or_404(
        DatasetProposal.objects.select_related("dataset", "base_release", "submitted_by"),
        pk=proposal_id,
    )
    profile = getattr(request.user, "profile", None)
    is_reviewer_or_admin = bool(profile and profile.has_role("reviewer", "admin"))
    if (
        proposal.submitted_by_id != request.user.pk
        and not proposal.dataset.is_owned_by(request.user)
        and not is_reviewer_or_admin
    ):
        return Response({"detail": "You cannot view this proposal."}, status=403)
    return Response(_proposal_payload(proposal))


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def cancel_proposal_view(request, proposal_id):
    proposal = get_object_or_404(DatasetProposal, pk=proposal_id)
    try:
        cancelled = cancel_proposal(proposal, request.user, request.data.get("reason", ""))
    except UnauthorizedProposalReviewerError as exc:
        return Response({"detail": str(exc)}, status=403)
    except ProposalLifecycleError as exc:
        return Response({"detail": str(exc)}, status=400)
    except ValueError as exc:
        return Response({"detail": str(exc)}, status=400)
    return Response({"id": str(cancelled.pk), "status": cancelled.status})


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def submit_proposal_view(request, proposal_id):
    proposal = get_object_or_404(DatasetProposal.objects.select_related("dataset"), pk=proposal_id)
    try:
        submitted = submit_proposal(proposal, request.user)
    except (ProposalLifecycleError, UnauthorizedProposalReviewerError, PublicationError, ValueError) as exc:
        return Response({"detail": str(exc)}, status=400)
    return Response({
        "id": str(submitted.id),
        "status": submitted.status,
        "computed_min_change_type": submitted.computed_min_change_type,
        "submitted_at": submitted.submitted_at,
    })


@api_view(["PATCH", "PUT"])
@permission_classes([IsAuthenticated])
def edit_proposal_view(request, proposal_id):
    proposal = get_object_or_404(DatasetProposal.objects.select_related("dataset"), pk=proposal_id)
    if not _can_edit_proposal(request.user, proposal):
        return Response({"detail": "Only the authorized proposal submitter may edit this proposal."}, status=403)
    allowed_fields = {
        "proposed_metadata", "proposed_license", "license_change_reason",
        "declared_change_type", "change_summary", "release_notes",
    }
    unknown = set(request.data.keys()) - allowed_fields
    if unknown:
        return Response({"detail": "Unsupported proposal fields.", "fields": sorted(unknown)}, status=400)
    edit_payload = request.data.copy()
    if request.method == "PATCH" and isinstance(edit_payload.get("proposed_metadata"), dict):
        merged_metadata = dict(proposal.proposed_metadata or {})
        merged_metadata.update(edit_payload["proposed_metadata"])
        edit_payload["proposed_metadata"] = merged_metadata
    if "proposed_metadata" in edit_payload and isinstance(edit_payload["proposed_metadata"], dict) and "category" in edit_payload["proposed_metadata"]:
        category_data = edit_payload["proposed_metadata"]["category"]
        if not isinstance(category_data, dict) or not category_data.get("id"):
            return Response({"detail": "category must include an approved category id."}, status=400)
        category_id = _parse_uuid(category_data["id"])
        if category_id is None:
            return Response({"detail": "category.id must be a valid UUID."}, status=400)
        category = get_object_or_404(Category, pk=category_id, status=Category.Status.APPROVED)
        edit_payload["proposed_metadata"] = dict(edit_payload["proposed_metadata"])
        edit_payload["proposed_metadata"]["category"] = {
            "id": str(category.pk), "name": category.name, "description": category.description or "",
        }
    try:
        updated = update_draft_proposal(
            proposal, request.user,
            **{key: edit_payload[key] for key in allowed_fields if key in edit_payload},
        )
    except (ProposalLifecycleError, UnauthorizedProposalReviewerError, ValueError, PublicationError) as exc:
        return Response({"detail": str(exc)}, status=400)
    return Response(_proposal_payload(updated))


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def revise_requested_proposal_view(request, proposal_id):
    proposal = get_object_or_404(DatasetProposal, pk=proposal_id)
    try:
        revised = resubmit_requested_proposal(proposal, request.user)
    except UnauthorizedProposalReviewerError as exc:
        return Response({"detail": str(exc)}, status=403)
    except ProposalLifecycleError as exc:
        return Response({"detail": str(exc)}, status=400)
    return Response({"id": str(revised.pk), "status": revised.status, "review_feedback": [
        item.reason for item in revised.activity.filter(action=DatasetProposalActivity.Action.CHANGES_REQUESTED)
    ]})


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def request_changes_proposal_view(request, proposal_id):
    proposal = get_object_or_404(DatasetProposal, pk=proposal_id)
    try:
        updated = request_proposal_changes(proposal, request.user, request.data.get("reason", ""))
    except UnauthorizedProposalReviewerError as exc:
        return Response({"detail": str(exc)}, status=403)
    except (ProposalLifecycleError, ValueError) as exc:
        return Response({"detail": str(exc)}, status=400)
    return Response(_proposal_payload(updated))


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def vote_proposal_view(request, proposal_id):
    proposal = get_object_or_404(DatasetProposal, pk=proposal_id)
    try:
        result = submit_proposal_vote(
            proposal,
            request.user,
            request.data.get("vote"),
            reason=request.data.get("reason", ""),
        )
    except (ProposalLifecycleError, UnauthorizedProposalReviewerError, ValueError) as exc:
        return Response({"detail": str(exc)}, status=400)
    return Response(result)


@api_view(["POST"])
@permission_classes([IsReviewerOrAdmin])
def publish_proposal_view(request, proposal_id):
    proposal = get_object_or_404(DatasetProposal, pk=proposal_id)
    final_change_type = request.data.get("final_change_type")
    try:
        release = publish_proposal(proposal, request.user, final_change_type)
    except (PublicationError, ValueError) as exc:
        return Response({"detail": str(exc)}, status=400)
    return Response(_release_payload(release), status=201)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def add_proposal_file_view(request, dataset_id, proposal_id):
    proposal = get_object_or_404(DatasetProposal, pk=proposal_id, dataset_id=dataset_id)
    if not _can_edit_proposal(request.user, proposal) or proposal.status != DatasetProposal.Status.DRAFT:
        return Response({"detail": "You cannot edit this proposal."}, status=403)
    stored_object_id = _parse_uuid(request.data.get("stored_object_id"))
    if stored_object_id is None:
        return Response({"detail": "stored_object_id must be a valid UUID."}, status=400)
    stored_object = get_object_or_404(StoredObject, pk=stored_object_id, dataset_id=dataset_id)
    logical_file_id = request.data.get("logical_file_id")
    if logical_file_id is not None:
        logical_file_id = _parse_uuid(logical_file_id)
        if logical_file_id is None:
            return Response({"detail": "logical_file_id must be a valid UUID."}, status=400)
    display_order = request.data.get("display_order")
    if display_order is not None:
        try:
            display_order = int(display_order)
        except (TypeError, ValueError):
            return Response({"detail": "display_order must be a non-negative integer."}, status=400)
        if display_order < 0:
            return Response({"detail": "display_order must be a non-negative integer."}, status=400)
    try:
        entry = add_proposal_file(
            proposal, stored_object,
            logical_file_id=logical_file_id,
            display_order=display_order,
        )
    except (PublicationError, ValueError, RuntimeError) as exc:
        return Response({"detail": str(exc)}, status=400)
    return Response({"logical_file_id": str(entry.logical_file_id)}, status=201)


@api_view(["PUT"])
@permission_classes([IsAuthenticated])
def replace_proposal_file_view(request, dataset_id, proposal_id, logical_file_id):
    proposal = get_object_or_404(DatasetProposal, pk=proposal_id, dataset_id=dataset_id)
    if not _can_edit_proposal(request.user, proposal) or proposal.status != DatasetProposal.Status.DRAFT:
        return Response({"detail": "You cannot edit this proposal."}, status=403)
    stored_object_id = _parse_uuid(request.data.get("stored_object_id"))
    if stored_object_id is None:
        return Response({"detail": "stored_object_id must be a valid UUID."}, status=400)
    stored_object = get_object_or_404(StoredObject, pk=stored_object_id, dataset_id=dataset_id)
    try:
        replace_proposal_file(proposal, logical_file_id, stored_object)
    except (PublicationError, ValueError, RuntimeError) as exc:
        return Response({"detail": str(exc)}, status=400)
    return Response({"status": "replaced"})


@api_view(["DELETE"])
@permission_classes([IsAuthenticated])
def remove_proposal_file_view(request, dataset_id, proposal_id, logical_file_id):
    proposal = get_object_or_404(DatasetProposal, pk=proposal_id, dataset_id=dataset_id)
    if not _can_edit_proposal(request.user, proposal) or proposal.status != DatasetProposal.Status.DRAFT:
        return Response({"detail": "You cannot edit this proposal."}, status=403)
    try:
        remove_proposal_file(proposal, logical_file_id)
    except (PublicationError, ValueError, RuntimeError) as exc:
        return Response({"detail": str(exc)}, status=400)
    return Response(status=204)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def init_proposal_upload_view(request, dataset_id, proposal_id):
    proposal = get_object_or_404(
        DatasetProposal, pk=proposal_id, dataset_id=dataset_id,
        status=DatasetProposal.Status.DRAFT,
    )
    if not _can_edit_proposal(request.user, proposal):
        return Response({"detail": "You cannot edit this proposal."}, status=403)
    session_id = uuid.uuid4().hex
    UploadSession.objects.create(
        id=session_id, dataset_id=dataset_id, uploader=request.user, proposal=proposal
    )
    os.makedirs(session_dir(session_id), exist_ok=True)
    return Response({"upload_session_id": session_id, "proposal_id": str(proposal.pk)}, status=201)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def complete_proposal_upload_view(request, dataset_id, proposal_id, upload_session_id):
    proposal = get_object_or_404(
        DatasetProposal, pk=proposal_id, dataset_id=dataset_id,
        status=DatasetProposal.Status.DRAFT,
    )
    if not _can_edit_proposal(request.user, proposal):
        return Response({"detail": "You cannot edit this proposal."}, status=403)
    session = get_object_or_404(
        UploadSession, pk=upload_session_id, dataset_id=dataset_id,
        uploader=request.user, proposal=proposal, completed_at__isnull=True,
    )
    temporary_dataset_file = None
    try:
        temporary_dataset_file = finalize_upload(
            dataset_id=dataset_id,
            upload_session_id=session.id,
            uploader=request.user,
            original_filename=request.data.get("filename", "proposal-file"),
            declared_file_type=request.data.get("file_type", "csv"),
        )
        logical_file_id = request.data.get("logical_file_id")
        action = request.data.get("action", "add")
        if action == "replace":
            logical_file_id = _parse_uuid(logical_file_id)
            if logical_file_id is None:
                raise ValueError("logical_file_id must be a valid UUID for replacement.")
            replace_proposal_file(proposal, logical_file_id, temporary_dataset_file.stored_object)
        elif action == "add":
            entry = add_proposal_file(
                proposal,
                temporary_dataset_file.stored_object,
                logical_file_id=temporary_dataset_file.pk,
                display_order=request.data.get("display_order"),
            )
            logical_file_id = entry.logical_file_id
        else:
            raise ValueError("action must be add or replace.")
        session.completed_at = timezone.now()
        session.save(update_fields=["completed_at"])
    except UploadTooLargeError:
        return Response({"detail": "File exceeds the configured size limit."}, status=413)
    except (MissingChunksError, FileNotFoundError) as exc:
        return Response({"detail": str(exc)}, status=400)
    except (ValueError, RuntimeError, PublicationError) as exc:
        return Response({"detail": str(exc)}, status=400)
    finally:
        if temporary_dataset_file is not None:
            # finalize_upload creates a V1 compatibility row. Keep the new
            # immutable StoredObject, but never expose this unapproved file in
            # the live V1 manifest.
            DatasetFile.objects.filter(pk=temporary_dataset_file.pk).delete()
    return Response({"status": action, "logical_file_id": str(logical_file_id)}, status=201)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def metadata_audit_history_view(request, dataset_id):
    dataset = get_object_or_404(Dataset, pk=dataset_id, is_active=True)
    profile = getattr(request.user, "profile", None)
    if not dataset.is_owned_by(request.user) and not (profile and profile.has_role("admin")):
        return Response({"detail": "Only the dataset owner or an administrator may view metadata audit history."}, status=403)
    audits = dataset.metadata_edit_audits.select_related("actor").all()
    return Response([{
        "id": str(item.id),
        "actor_id": item.actor_id,
        "actor_name": (
            getattr(getattr(item.actor, "profile", None), "full_name", "")
            if item.actor_id else None
        ),
        "changed_fields": item.changed_fields,
        "before": item.before_snapshot,
        "after": item.after_snapshot,
        "created_at": item.created_at,
    } for item in audits])


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def release_history_view(request, dataset_id):
    dataset = get_object_or_404(Dataset, pk=dataset_id, is_active=True)
    if not _can_read_dataset(request.user, dataset):
        return Response({"detail": "Not found."}, status=404)
    releases = dataset.releases.filter(status__in=[
        DatasetRelease.Status.PUBLISHED, DatasetRelease.Status.WITHDRAWN,
    ]).order_by("-major", "-minor", "-patch")
    return Response([_release_payload(item, include_snapshot=False) for item in releases])


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def release_detail_view(request, dataset_id, release_id):
    dataset = get_object_or_404(Dataset, pk=dataset_id, is_active=True)
    if not _can_read_dataset(request.user, dataset):
        return Response({"detail": "Not found."}, status=404)
    release = get_object_or_404(
        DatasetRelease.objects.prefetch_related("files__stored_object"),
        pk=release_id, dataset=dataset,
        status__in=[DatasetRelease.Status.PUBLISHED, DatasetRelease.Status.WITHDRAWN],
    )
    return Response(_release_payload(release))


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def release_file_download_view(request, dataset_id, release_id, logical_file_id):
    """Stream a verified file from the selected immutable release manifest."""
    from django.http import FileResponse
    from apps.datasets.services.file_integrity import (
        open_verified_object_for_download, StorageVerificationError,
    )

    dataset = get_object_or_404(Dataset, pk=dataset_id, is_active=True)
    if not _can_read_dataset(request.user, dataset):
        return Response({"detail": "Not found."}, status=404)
    release = get_object_or_404(
        DatasetRelease.objects.prefetch_related("files__stored_object"),
        pk=release_id, dataset=dataset,
        status__in=[DatasetRelease.Status.PUBLISHED, DatasetRelease.Status.WITHDRAWN],
    )
    entry = release.files.select_related("stored_object").filter(
        logical_file_id=logical_file_id
    ).first()
    if entry is None:
        return Response({"detail": "File not found in this release."}, status=404)
    stored = entry.stored_object
    if stored.dataset_id != dataset.pk:
        return Response({"detail": "File not found."}, status=404)
    try:
        # One object-store read is staged locally, verified, and streamed. No
        # storage key, bucket name, or provider credentials are returned.
        verified_file = open_verified_object_for_download(stored)
    except StorageVerificationError:
        return Response({"detail": "The release file is unavailable or failed integrity verification."}, status=503)
    except Exception:
        return Response({"detail": "The release file is temporarily unavailable."}, status=503)
    return FileResponse(
        verified_file,
        as_attachment=True,
        filename=stored.original_filename,
        content_type="application/octet-stream",
    )



@api_view(["GET"])
@permission_classes([IsAuthenticated])
def pid_metadata_view(request, dataset_id, identifier_id):
    """Preview provider-independent DataCite attributes for an authorized PID."""
    from django.core.exceptions import ValidationError
    from apps.datasets.services.pid_metadata import build_datacite_metadata

    dataset = get_object_or_404(Dataset, pk=dataset_id, is_active=True)
    if not _can_read_dataset(request.user, dataset):
        return Response({"detail": "Not found."}, status=404)
    identifier = get_object_or_404(
        ResourceIdentifier.objects.select_related("release"),
        pk=identifier_id,
    )
    identifier_dataset_id = (
        identifier.dataset_id if identifier.dataset_id else identifier.release.dataset_id
    )
    if identifier_dataset_id != dataset.pk:
        return Response({"detail": "Not found."}, status=404)
    try:
        attributes = build_datacite_metadata(identifier)
    except ValidationError as exc:
        return Response({
            "ready": False,
            "identifier_id": str(identifier.pk),
            "role": identifier.role,
            "issues": exc.messages,
        }, status=409)
    return Response({
        "ready": True,
        "identifier_id": str(identifier.pk),
        "role": identifier.role,
        "registered": identifier.registration_status == ResourceIdentifier.RegistrationStatus.REGISTERED,
        "attributes": attributes,
    })
