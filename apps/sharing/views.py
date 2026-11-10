from django.contrib.auth import get_user_model
from django.db import models as django_models, transaction
from django.shortcuts import get_object_or_404
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response


from apps.accounts.models import ActivityLog
from apps.datasets.models import Dataset, Contributor, DatasetInvitation, PermissionLevel
from apps.datasets.permissions import IsDatasetOwner
from apps.datasets.services.storage import presigned_download_url
from apps.datasets.services.invitations import create_invitation, accept_invitation
from apps.notifications.services import notify
from apps.notifications.models import Notification
from apps.access_control.models import AccessRequest, AccessRequestPermission, Permission
from apps.access_control.services import (
    _system_grant_user,
    grant_to_user,
    has_permission as has_scoped_permission,
)

from .models import UsabilityFormResponse, RestrictedAccessJustification, DatasetAccessRequest
from .serializers import RequestAccessSerializer
from apps.accounts.utils import is_institutional_email
from .services import (
    user_can_freely_download, user_can_access_dataset, user_meets_access_requirements,
    claim_share_access,
)

User = get_user_model()

_LEGACY_REVIEW_GONE = {
    "detail": "Access request reviews moved to /api/access-control/requests/",
}


def _download_action_for(user, dataset, is_reviewer=False):
    if dataset.owner_id == user.id:
        return "owner_download"
    if has_scoped_permission(user, "user.manage"):
        return "admin_download"
    if is_reviewer:
        return "reviewer_download"
    if Contributor.objects.filter(dataset=dataset, user=user).exists():
        return "contributor_download"
    return "dataset_download"


def _download_url_response(request, dataset, is_reviewer=False):
    if dataset.current_version is None:
        return Response({"detail": "This dataset has no published file yet."}, status=404)

    ActivityLog.objects.create(
        user=request.user,
        action=_download_action_for(request.user, dataset, is_reviewer=is_reviewer),
        target_object=f"Dataset:{dataset.id}",
        ip_address=request.META.get("REMOTE_ADDR", "unknown"),
    )
    Dataset.objects.filter(id=dataset.id).update(download_count=django_models.F("download_count") + 1)

    return Response({"download_url": presigned_download_url(dataset.current_version.file_key)})


def _actor_can_grant_dataset_access(actor, dataset):
    return has_scoped_permission(actor, "user.permission.grant") and (
        has_scoped_permission(actor, "user.manage")
        or has_scoped_permission(actor, "dataset.download", "dataset", dataset.id)
    )


def _grant_view_and_download(actor, user, dataset, expires_at=None):
    for code in ("dataset.view", "dataset.download"):
        permission = Permission.objects.get(pk=code)
        try:
            grant_to_user(actor, user, permission, "dataset", dataset.id, expires_at=expires_at)
        except PermissionError:
            _system_grant_user(user, code, "dataset", dataset.id, expires_at=expires_at, actor=actor)
        except ValueError:
            # Active grant already exists — treat as success.
            pass


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def download_dataset(request, dataset_id):
    dataset = get_object_or_404(Dataset, id=dataset_id, is_active=True)
    scoped_download = has_scoped_permission(request.user, "dataset.download", "dataset", dataset.id)
    is_manager = has_scoped_permission(request.user, "user.manage")
    if (
        dataset.visibility == Dataset.Visibility.PRIVATE
        and dataset.owner_id != request.user.id
        and not scoped_download
        and not is_manager
    ):
        return Response({"detail": "You don't have access to this dataset."}, status=403)

    if not user_meets_access_requirements(request.user):
        return Response(
            {"detail": "Please complete your profile before downloading datasets."}, status=403,
        )

    is_reviewer = has_scoped_permission(request.user, "dataset.approve")
    is_free_access = user_can_freely_download(request.user, dataset)

    can_download = is_free_access or scoped_download or is_manager
    if not can_download:
        return Response({"detail": "You don't have access to this dataset."}, status=403)

    return _download_url_response(request, dataset, is_reviewer=is_reviewer)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def request_share_access(request, dataset_id):
    dataset = get_object_or_404(Dataset, id=dataset_id, is_active=True)
    if dataset.visibility == Dataset.Visibility.PRIVATE:
        return Response({"detail": "This dataset is private."}, status=404)

    serializer = RequestAccessSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)

    UsabilityFormResponse.objects.create(
        dataset=dataset, user=request.user, purpose=serializer.validated_data["purpose"],
    )

    if dataset.visibility == Dataset.Visibility.PUBLIC:
        if not user_meets_access_requirements(request.user):
            return Response(
                {"detail": "Please complete your profile before accessing this dataset."}, status=403,
            )
        for code in ("dataset.view", "dataset.download"):
            _system_grant_user(request.user, code, "dataset", dataset.id, actor=None)
        response = _download_url_response(request, dataset)
        if response.status_code != 200:
            return response
        response.data["status"] = "approved"
        response.data["share_ready"] = True
        return response

    if not request.user.profile.is_profile_complete():
        return Response(
            {"detail": "Please complete your profile before requesting access to a restricted dataset."},
            status=403,
        )

    justification_text = (serializer.validated_data.get("justification") or "").strip()
    if not justification_text:
        return Response({"detail": "Please complete the restricted-access justification form."}, status=400)

    purpose = serializer.validated_data["purpose"]
    justification = f"{purpose}\n\n{justification_text}".strip()
    RestrictedAccessJustification.objects.create(
        dataset=dataset, requester=request.user, justification=justification_text,
    )
    with transaction.atomic():
        access_request = AccessRequest.objects.create(
            requester=request.user,
            kind=AccessRequest.Kind.PERMISSION,
            resource_type="dataset",
            resource_id=dataset.id,
            justification=justification,
        )
        AccessRequestPermission.objects.bulk_create([
            AccessRequestPermission(request=access_request, permission_id="dataset.view"),
            AccessRequestPermission(request=access_request, permission_id="dataset.download"),
        ])

    notify(
        user=dataset.owner, notification_type=Notification.NotificationType.ACCESS_REQUEST,
        message=(
            f'{request.user.profile.full_name} requested sharing access to your dataset '
            f'"{dataset.title}". Review it in Access Management.'
        ),
        dataset=dataset, link_path=f"/access-management/requests/{access_request.id}",
    )
    return Response({"status": "pending", "request_id": access_request.id})


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def share_with_user(request, dataset_id):
    """Grant access to an existing user when authorized, or create a claim-token
    invite via DatasetAccessRequest when the email has no account yet."""
    dataset = get_object_or_404(Dataset, id=dataset_id, is_active=True)
    if not user_can_access_dataset(request.user, dataset):
        return Response({"detail": "You don't have access to this dataset to share it."}, status=403)

    recipient_email = (request.data.get("email") or "").strip().lower()
    if not recipient_email:
        return Response({"detail": "email is required."}, status=400)
    if not is_institutional_email(recipient_email):
        return Response({"detail": "You can only share datasets with an AASTU email address."}, status=400)

    recipient = User.objects.filter(email__iexact=recipient_email).first()
    if recipient and recipient.id == request.user.id:
        return Response({"detail": "You can't share a dataset with yourself."}, status=400)

    if dataset.visibility == Dataset.Visibility.PRIVATE:
        return Response({"detail": "Private datasets can't be shared."}, status=403)

    serializer = RequestAccessSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)

    if recipient:
        if not _actor_can_grant_dataset_access(request.user, dataset):
            return Response(
                {"detail": "You don't have permission to grant access to this dataset."},
                status=403,
            )
        expires_at = None
        duration = serializer.validated_data.get("requested_duration_days")
        if duration:
            from django.utils import timezone
            from datetime import timedelta
            expires_at = timezone.now() + timedelta(days=duration)
        UsabilityFormResponse.objects.create(
            dataset=dataset, user=recipient, purpose=serializer.validated_data["purpose"],
        )
        _grant_view_and_download(request.user, recipient, dataset, expires_at=expires_at)
        notify(
            user=recipient, notification_type=Notification.NotificationType.DATASET_APPROVED,
            message=f'{request.user.profile.full_name} shared "{dataset.title}" with you.',
            dataset=dataset, link_path=f"/datasets/{dataset.id}",
        )
        return Response({"status": "granted", "user_id": recipient.id})

    # Email-only invite: DatasetAccessRequest is claim-token vehicle only (no votes).
    usability_form = UsabilityFormResponse.objects.create(
        dataset=dataset, user=None, email=recipient_email,
        purpose=serializer.validated_data["purpose"],
    )
    justification = None
    justification_text = (serializer.validated_data.get("justification") or "").strip()
    if dataset.visibility == Dataset.Visibility.RESTRICTED:
        if not justification_text:
            return Response({"detail": "Please complete the restricted-access justification form."}, status=400)
        justification = RestrictedAccessJustification.objects.create(
            dataset=dataset, requester=None, email=recipient_email, justification=justification_text,
        )

    access_request = DatasetAccessRequest.objects.create(
        dataset=dataset, requester=None, requester_email=recipient_email, shared_by=request.user,
        usability_form=usability_form, restricted_justification=justification,
        purpose_type=serializer.validated_data["purpose_type"],
        requested_duration_days=serializer.validated_data.get("requested_duration_days"),
        status=DatasetAccessRequest.Status.PENDING,
        owner_decision=DatasetAccessRequest.OwnerDecision.APPROVED,
    )
    from .services import _approve
    _approve(access_request)
    return Response({"status": "approved", "claim_required": True})


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def access_request_queue(request):
    return Response(_LEGACY_REVIEW_GONE, status=410)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def vote_on_access_request(request, request_id):
    return Response(_LEGACY_REVIEW_GONE, status=410)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def owner_decide_access_request(request, request_id):
    return Response(_LEGACY_REVIEW_GONE, status=410)


@api_view(["POST"])
@permission_classes([IsAuthenticated, IsDatasetOwner])
def invite_coauthor(request, dataset_id):
    """Owner enters an email (or picks a user the frontend looked up by
    name/email) and a permission level. The invitation is created right away
    — it's only actually emailed to the invitee once the dataset is approved;
"""
    dataset = get_object_or_404(Dataset, id=dataset_id)
    email = (request.data.get("email") or "").strip()
    if not email:
        return Response({"detail": "email is required."}, status=400)

    permission = (request.data.get("permission") or PermissionLevel.VIEW).strip().lower()
    if permission not in PermissionLevel.values:
        return Response({"detail": "permission must be 'edit' or 'view'."}, status=400)

    invitation = create_invitation(
        dataset, request.user, email, DatasetInvitation.Role.CO_AUTHOR, permission,
    )
    existing_user = User.objects.filter(email__iexact=email).select_related("profile").first()
    return Response({
        "status": "invited",
        "invitation_id": invitation.id,
        "permission": invitation.permission,
        "invited_email": invitation.invited_email,
        "matched_user": {
            "id": existing_user.id,
            "email": existing_user.email,
            "full_name": existing_user.profile.full_name if hasattr(existing_user, "profile") else "",
        } if existing_user else None,
    }, status=201)





@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated])
def view_invitation(request, token):
    invitation = get_object_or_404(DatasetInvitation, token=token)

    if request.method == "GET":
        return Response({
            "dataset_title": invitation.dataset.title, "role": invitation.role,
            "invited_by": invitation.invited_by.profile.full_name,
            "status": invitation.status, "expires_at": invitation.expires_at,
            "valid": invitation.is_valid(),
        })

    try:
        contributor = accept_invitation(token, request.user)
    except ValueError as exc:
        return Response({"detail": str(exc)}, status=400)
    return Response({"status": "accepted", "contributor_type": contributor.contributor_type})


@api_view(["POST"])
@permission_classes([IsAuthenticated, IsDatasetOwner])
def revoke_invitation(request, dataset_id, invitation_id):
    invitation = get_object_or_404(DatasetInvitation, id=invitation_id, dataset_id=dataset_id)
    if invitation.status != DatasetInvitation.Status.PENDING:
        return Response({"detail": "Only a pending invitation can be revoked."}, status=400)
    invitation.status = DatasetInvitation.Status.REVOKED
    invitation.save(update_fields=["status"])
    return Response({"status": "revoked"})



@api_view(["POST"])
@permission_classes([IsAuthenticated])
def claim_access(request, token):
    try:
        dataset = claim_share_access(token, request.user, purpose=request.data.get("purpose"))
    except ValueError as exc:
        return Response({"detail": str(exc)}, status=400)
    return Response({"status": "claimed", "dataset_id": dataset.id})
