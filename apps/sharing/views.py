from django.contrib.auth import get_user_model
from django.db import models as django_models
from django.shortcuts import get_object_or_404
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response


from apps.accounts.models import ActivityLog
from apps.accounts.permissions import IsReviewerOrAdmin
from apps.datasets.models import Dataset, Contributor, DatasetInvitation, PermissionLevel
from apps.datasets.permissions import IsDatasetOwner
from apps.datasets.services.storage import presigned_download_url
from apps.datasets.services.invitations import create_invitation, accept_invitation
from apps.notifications.services import notify
from apps.notifications.models import Notification

from .models import UsabilityFormResponse, RestrictedAccessJustification, DatasetAccessRequest, AccessRequestVote, SharePermission
from .serializers import RequestAccessSerializer, DatasetAccessRequestSerializer
from apps.accounts.utils import is_institutional_email
from .services import user_can_freely_download, user_can_access_dataset, resolve_access_request_votes, record_owner_decision, claim_share_access

User = get_user_model()




@api_view(["GET"])
@permission_classes([IsAuthenticated])
def download_dataset(request, dataset_id):
    dataset = get_object_or_404(Dataset, id=dataset_id, is_active=True)
    if dataset.visibility == Dataset.Visibility.PRIVATE and dataset.owner_id != request.user.id:
        return Response({"detail": "You don't have access to this dataset."}, status=403)
    profile = getattr(request.user, "profile", None)
    is_reviewer = profile.has_role("reviewer", "admin")
    is_free_access = user_can_freely_download(request.user, dataset)
    reviewer_bypass = is_reviewer and dataset.visibility == Dataset.Visibility.PUBLIC
    permission = SharePermission.objects.filter(dataset=dataset, shared_with_user=request.user).first()
    has_active_share = bool(permission and permission.is_active_grant())

    has_permission = is_free_access or reviewer_bypass or has_active_share
    if not has_permission:
        return Response({"detail": "You don't have access to this dataset."}, status=403)
    if dataset.current_version is None:
        return Response({"detail": "This dataset has no published file yet."}, status=404)

    if dataset.owner_id == request.user.id:
        action = "owner_download"
    elif is_reviewer:
        action = "reviewer_download"
    elif Contributor.objects.filter(dataset=dataset, user=request.user).exists():
        action = "contributor_download"
    else:
        action = "dataset_download"

    ActivityLog.objects.create(
        user=request.user, action=action, target_object=f"Dataset:{dataset.id}",
        ip_address=request.META.get("REMOTE_ADDR", "unknown"),
    )
    Dataset.objects.filter(id=dataset.id).update(download_count=django_models.F("download_count") + 1)

    return Response({"download_url": presigned_download_url(dataset.current_version.file_key)})

@api_view(["POST"])
@permission_classes([IsAuthenticated])
def request_share_access(request, dataset_id):
    dataset = get_object_or_404(Dataset, id=dataset_id, is_active=True)
    if dataset.visibility == Dataset.Visibility.PRIVATE:
        return Response({"detail": "This dataset is private."}, status=404)

    serializer = RequestAccessSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)

    usability_form = UsabilityFormResponse.objects.create(
        dataset=dataset, user=request.user, purpose=serializer.validated_data["purpose"],
    )

    if dataset.visibility == Dataset.Visibility.PUBLIC:
        SharePermission.objects.get_or_create(dataset=dataset, shared_with_user=request.user,
                                               defaults={"access_type": "download"})
        return Response({"status": "approved", "share_ready": True})

    if not request.user.profile.is_profile_complete():
        return Response(
            {"detail": "Please complete your profile before requesting access to a restricted dataset."},
            status=403,
        )

    justification_text = (serializer.validated_data.get("justification") or "").strip()
    if not justification_text:
        return Response({"detail": "Please complete the restricted-access justification form."}, status=400)

    justification = RestrictedAccessJustification.objects.create(
        dataset=dataset, requester=request.user, justification=justification_text,
    )
    access_request = DatasetAccessRequest.objects.create(
    dataset=dataset,
    requester=request.user,
    requester_email=request.user.email,
    usability_form=usability_form,
    restricted_justification=justification,
    purpose_type=serializer.validated_data["purpose_type"],
    requested_duration_days=serializer.validated_data.get("requested_duration_days"),
)

    for reviewer in User.objects.filter(profile__roles__role__in=["reviewer", "admin"]).distinct():
        notify(
            user=reviewer, notification_type=Notification.NotificationType.ACCESS_REQUEST,
            message=f'{request.user.profile.full_name} requested sharing access to "{dataset.title}".',
            dataset=dataset, link_path=f"/admin-panel/access-requests/{access_request.id}",
        )
    notify(
        user=dataset.owner, notification_type=Notification.NotificationType.ACCESS_REQUEST,
        message=f'{request.user.profile.full_name} requested sharing access to your dataset "{dataset.title}". Your approval is required.',
        dataset=dataset, link_path=f"/datasets/{dataset.id}/access-requests/{access_request.id}",
    )
    return Response({"status": "pending", "request_id": access_request.id})


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def share_with_user(request, dataset_id):
    """Distinct from request_share_access: the ACTING user must already have
    access, and is granting access to someone ELSE by email. The recipient
    doesn't need an existing account — see claim_share_access."""
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

    usability_form = UsabilityFormResponse.objects.create(
        dataset=dataset, user=recipient, email="" if recipient else recipient_email,
        purpose=serializer.validated_data["purpose"],
    )

    if dataset.visibility == Dataset.Visibility.PUBLIC:
        access_request = DatasetAccessRequest.objects.create(
            dataset=dataset, requester=recipient, requester_email=recipient_email, shared_by=request.user,
            usability_form=usability_form, purpose_type=serializer.validated_data["purpose_type"],
            requested_duration_days=serializer.validated_data.get("requested_duration_days"),
            status=DatasetAccessRequest.Status.PENDING, owner_decision=DatasetAccessRequest.OwnerDecision.APPROVED,
        )
        from .services import _approve
        _approve(access_request)
        return Response({"status": "approved", "share_ready": bool(recipient)})

    # Restricted
    if recipient and not recipient.profile.is_profile_complete():
        return Response(
            {"detail": f"{recipient.profile.full_name} needs to complete their profile before receiving access to a restricted dataset."},
            status=400,
        )

    justification_text = (serializer.validated_data.get("justification") or "").strip()
    if not justification_text:
        return Response({"detail": "Please complete the restricted-access justification form."}, status=400)

    justification = RestrictedAccessJustification.objects.create(
        dataset=dataset, requester=recipient, email="" if recipient else recipient_email,
        justification=justification_text,
    )
    access_request = DatasetAccessRequest.objects.create(
        dataset=dataset, requester=recipient, requester_email=recipient_email, shared_by=request.user,
        usability_form=usability_form, restricted_justification=justification,
        purpose_type=serializer.validated_data["purpose_type"],
        requested_duration_days=serializer.validated_data.get("requested_duration_days"),
        owner_decision=(
            DatasetAccessRequest.OwnerDecision.APPROVED if dataset.owner_id == request.user.id
            else DatasetAccessRequest.OwnerDecision.PENDING
        ),
    )
    resolve_access_request_votes(access_request)

    for reviewer in User.objects.filter(profile__roles__role__in=["reviewer", "admin"]).distinct():
        notify(
            user=reviewer, notification_type=Notification.NotificationType.ACCESS_REQUEST,
            message=f'{request.user.profile.full_name} requested sharing "{dataset.title}" with {recipient_email}.',
            dataset=dataset, link_path=f"/admin-panel/access-requests/{access_request.id}",
        )
    if dataset.owner_id != request.user.id:
        notify(
            user=dataset.owner, notification_type=Notification.NotificationType.ACCESS_REQUEST,
            message=f'{request.user.profile.full_name} wants to share your dataset "{dataset.title}" with {recipient_email}. Your approval is required.',
            dataset=dataset, link_path=f"/datasets/{dataset.id}/access-requests/{access_request.id}",
        )
    return Response({"status": "pending", "request_id": access_request.id})


@api_view(["GET"])
@permission_classes([IsReviewerOrAdmin])
def access_request_queue(request):
    qs = DatasetAccessRequest.objects.filter(status=DatasetAccessRequest.Status.PENDING).select_related(
        "dataset", "requester__profile", "restricted_justification"
    )
    return Response(DatasetAccessRequestSerializer(qs, many=True).data)


@api_view(["POST"])
@permission_classes([IsReviewerOrAdmin])
def vote_on_access_request(request, request_id):
    access_request = get_object_or_404(DatasetAccessRequest, id=request_id)
    if access_request.status != DatasetAccessRequest.Status.PENDING:
        return Response({"detail": "This request has already been resolved."}, status=400)

    vote_value = request.data.get("vote")
    if vote_value not in ("approve", "reject"):
        return Response({"detail": "vote must be 'approve' or 'reject'."}, status=400)

    AccessRequestVote.objects.update_or_create(
        access_request=access_request, reviewer=request.user, defaults={"vote": vote_value}
    )
    result = resolve_access_request_votes(access_request)
    return Response(result)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def owner_decide_access_request(request, request_id):
    access_request = get_object_or_404(DatasetAccessRequest, id=request_id)
    if not access_request.dataset.is_owned_by(request.user):
        return Response({"detail": "Only the dataset owner can decide this."}, status=403)

    decision = request.data.get("decision")
    if decision not in ("approve", "reject"):
        return Response({"detail": "decision must be 'approve' or 'reject'."}, status=400)

    try:
        result = record_owner_decision(access_request, decision)
    except ValueError as exc:
        return Response({"detail": str(exc)}, status=400)
    return Response(result)


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

    permission = (request.data.get("permission") or PermissionLevel.EDIT).strip().lower()
    if permission not in PermissionLevel.values:
        return Response({"detail": "permission must be 'edit' or 'view'."}, status=400)

    invitation = create_invitation(
        dataset, request.user, email, DatasetInvitation.Role.CO_AUTHOR, permission,
    )
    return Response({"status": "invited", "invitation_id": invitation.id}, status=201)





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
        dataset = claim_share_access(token, request.user)
    except ValueError as exc:
        return Response({"detail": str(exc)}, status=400)
    return Response({"status": "claimed", "dataset_id": dataset.id})