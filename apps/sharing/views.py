from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.mail import send_mail
from django.db import models as django_models
from django.shortcuts import get_object_or_404
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.accounts.models import ActivityLog
from apps.accounts.permissions import IsCheckerOrAdmin
from apps.datasets.models import Dataset, Contributor
from apps.datasets.permissions import IsDatasetOwnerOrContributor
from apps.datasets.services.storage import presigned_download_url
from apps.notifications.services import notify
from apps.notifications.models import Notification

from .models import UsabilityFormResponse, RestrictedAccessJustification, DatasetAccessRequest, AccessRequestVote, SharePermission
from .serializers import RequestAccessSerializer, DatasetAccessRequestSerializer, InviteContributorSerializer
from .services import user_can_freely_download, resolve_access_request_votes

User = get_user_model()


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def view_dataset(request, dataset_id):
    """Increments view_count AND logs a timestamped event, so views can be
    graphed over time the same way downloads already can."""
    dataset = get_object_or_404(Dataset, id=dataset_id, is_active=True)
    Dataset.objects.filter(id=dataset.id).update(view_count=django_models.F("view_count") + 1)
    dataset.refresh_from_db(fields=["view_count"])
    ActivityLog.objects.create(
        user=request.user, action="dataset_view", target_object=f"Dataset:{dataset.id}",
        ip_address=request.META.get("REMOTE_ADDR", "unknown"),
    )
    return Response({"view_count": dataset.view_count})


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def download_dataset(request, dataset_id):
    """Free for the owner or a researcher-role contributor on their own dataset,
    any visibility. Everyone else needs an approved SharePermission — from
    request_share_access, resolved by reviewer committee vote for restricted data."""
    dataset = get_object_or_404(Dataset, id=dataset_id, is_active=True)
    profile = getattr(request.user, "profile", None)
    is_reviewer = profile.has_role("checker", "admin")
    is_free_access = user_can_freely_download(request.user, dataset)

    has_permission = (
        is_free_access or is_reviewer
        or SharePermission.objects.filter(dataset=dataset, shared_with_user=request.user).exists()
    )
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
    """Sharing ALWAYS requires this flow, even for the owner — that's the whole
    point of the distinction from download. public/institutional resolve instantly;
    restricted goes to the reviewer committee vote."""
    dataset = get_object_or_404(Dataset, id=dataset_id, is_active=True)
    serializer = RequestAccessSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)

    usability_form = UsabilityFormResponse.objects.create(
        dataset=dataset, user=request.user, purpose=serializer.validated_data["purpose"],
    )

    if dataset.visibility in (Dataset.Visibility.PUBLIC, Dataset.Visibility.INSTITUTIONAL):
        SharePermission.objects.get_or_create(dataset=dataset, shared_with_user=request.user,
                                               defaults={"access_type": "download"})
        return Response({"status": "approved", "share_ready": True})

    justification_text = (serializer.validated_data.get("justification") or "").strip()
    if not justification_text:
        return Response({"detail": "Please complete the restricted-access justification form."}, status=400)

    justification = RestrictedAccessJustification.objects.create(
        dataset=dataset, requester=request.user, justification=justification_text,
    )
    access_request = DatasetAccessRequest.objects.create(
        dataset=dataset, requester=request.user, usability_form=usability_form,
        restricted_justification=justification, purpose_type=serializer.validated_data["purpose_type"],
    )

    for reviewer in User.objects.filter(profile__roles__role__in=["checker", "admin"]).distinct():
        notify(
            user=reviewer, notification_type=Notification.NotificationType.ACCESS_REQUEST,
            message=f'{request.user.profile.full_name} requested sharing access to "{dataset.title}".',
            dataset=dataset, link_path=f"/admin-panel/access-requests/{access_request.id}",
        )
    return Response({"status": "pending", "request_id": access_request.id})


@api_view(["GET"])
@permission_classes([IsCheckerOrAdmin])
def access_request_queue(request):
    """Every pending share request awaiting committee votes."""
    qs = DatasetAccessRequest.objects.filter(status=DatasetAccessRequest.Status.PENDING).select_related(
        "dataset", "requester__profile", "restricted_justification"
    )
    return Response(DatasetAccessRequestSerializer(qs, many=True).data)


@api_view(["POST"])
@permission_classes([IsCheckerOrAdmin])
def vote_on_access_request(request, request_id):
    """One vote per reviewer — re-voting updates their existing vote rather than
    stacking duplicates. Resolves automatically once quorum + majority is reached."""
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


def _send_registration_invite_email(email, dataset, inviter):
    register_link = f"{settings.FRONTEND_URL}/register?invited_dataset={dataset.id}"
    send_mail(
        subject=f'You\'ve been invited to contribute on "{dataset.title}"',
        message=(
            f'{inviter.profile.full_name} added you as a contributor on "{dataset.title}" on ORDP.\n'
            f"You don't have an ORDP account yet — register with this email address to get access: {register_link}"
        ),
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[email],
    )


@api_view(["POST"])
@permission_classes([IsAuthenticated, IsDatasetOwnerOrContributor])
def invite_contributor(request, dataset_id):
    """Credits someone on the dataset. Does NOT grant researcher role or edit rights
    by itself — edit rights still require the invitee to independently hold
    role=researcher, per IsDatasetOwnerOrContributor."""
    dataset = get_object_or_404(Dataset, id=dataset_id)
    serializer = InviteContributorSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    email = serializer.validated_data["email"]

    try:
        invited_user = User.objects.get(email=email)
    except User.DoesNotExist:
        invited_user = None

    contributor = Contributor.objects.create(
        dataset=dataset, user=invited_user,
        name=invited_user.profile.full_name if invited_user else "",
        invited_email="" if invited_user else email,
        contributor_type=serializer.validated_data["contributor_type"],
        order=dataset.contributors.count() + 1,
    )

    if invited_user:
        SharePermission.objects.get_or_create(
            dataset=dataset, shared_with_user=invited_user, defaults={"access_type": "download"}
        )
        notify(
            user=invited_user, notification_type=Notification.NotificationType.CONTRIBUTOR_INVITATION,
            message=f'{request.user.profile.full_name} added you as a contributor on "{dataset.title}".',
            dataset=dataset, link_path=f"/datasets/{dataset.id}",
        )
        return Response({"status": "invited"}, status=201)

    _send_registration_invite_email(email, dataset, request.user)
    return Response({"status": "invited_pending_registration"}, status=201)