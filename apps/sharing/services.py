from datetime import timedelta

from django.conf import settings as dj_settings
from django.contrib.auth import get_user_model
from django.core.mail import send_mail
from django.utils import timezone

from apps.datasets.models import Dataset, Contributor
from apps.datasets.models import generate_invitation_token
from apps.notifications.services import notify
from apps.notifications.models import Notification
from .models import SharePermission, DatasetAccessRequest

User = get_user_model()

MIN_REVIEWER_QUORUM = 3
CLAIM_EXPIRY_DAYS = 14


def user_can_freely_download(user, dataset):
    if not user or not user.is_authenticated:
        return False

    if dataset.visibility == Dataset.Visibility.PRIVATE:
        return dataset.owner_id == user.id


    if dataset.owner_id == user.id:
        return True

    is_collaborator = Contributor.objects.filter(dataset=dataset, user=user).exists()
    if not is_collaborator:
        return False

    if dataset.visibility == Dataset.Visibility.RESTRICTED:
        profile = getattr(user, "profile", None)
        if not profile or not profile.is_profile_complete():
            return False

    return True


def user_can_access_dataset(user, dataset):
    """Broader check used for version-history / metadata reads / share-eligibility.
    Only counts an ACTIVE (non-expired, non-revoked) SharePermission."""
    if dataset.visibility == Dataset.Visibility.PRIVATE:
        return bool(user and user.is_authenticated and dataset.owner_id == user.id)

    profile = getattr(user, "profile", None)
    is_reviewer = bool(profile and profile.has_role("reviewer", "admin")) and dataset.visibility == Dataset.Visibility.PUBLIC
    if user_can_freely_download(user, dataset) or is_reviewer:
        return True
    permission = SharePermission.objects.filter(dataset=dataset, shared_with_user=user).first()
    return bool(permission and permission.is_active_grant())


def resolve_access_request_votes(access_request: DatasetAccessRequest):
    if access_request.status != DatasetAccessRequest.Status.PENDING:
        return {"status": access_request.status}

    if access_request.owner_decision == DatasetAccessRequest.OwnerDecision.REJECTED:
        _reject(access_request)
        return {"status": "rejected", "reason": "owner_declined"}

    total_reviewers = User.objects.filter(profile__roles__role__in=["reviewer", "admin"]).distinct().count()
    quorum = min(MIN_REVIEWER_QUORUM, total_reviewers) or 1
    approve_votes = access_request.votes.filter(vote="approve").count()
    reject_votes = access_request.votes.filter(vote="reject").count()
    votes_cast = approve_votes + reject_votes

    if votes_cast >= quorum and reject_votes > approve_votes:
        _reject(access_request)
        return {"status": "rejected", "approve_votes": approve_votes, "reject_votes": reject_votes}

    committee_approved = votes_cast >= quorum and approve_votes > reject_votes
    owner_approved = access_request.owner_decision == DatasetAccessRequest.OwnerDecision.APPROVED

    if committee_approved and owner_approved:
        _approve(access_request)
        return {"status": "approved", "approve_votes": approve_votes, "reject_votes": reject_votes}

    return {
        "status": "pending", "approve_votes": approve_votes, "reject_votes": reject_votes,
        "quorum": quorum, "committee_approved": committee_approved,
        "owner_decision": access_request.owner_decision,
    }


def record_owner_decision(access_request, decision):
    if access_request.status != DatasetAccessRequest.Status.PENDING:
        raise ValueError("This request has already been resolved.")
    access_request.owner_decision = (
        DatasetAccessRequest.OwnerDecision.APPROVED if decision == "approve"
        else DatasetAccessRequest.OwnerDecision.REJECTED
    )
    access_request.save(update_fields=["owner_decision"])
    return resolve_access_request_votes(access_request)


def _approve(access_request):
    access_request.status = DatasetAccessRequest.Status.APPROVED
    access_request.resolved_at = timezone.now()

    if access_request.requester_id:
        expires_at = None
        if access_request.requested_duration_days:
            expires_at = timezone.now() + timedelta(days=access_request.requested_duration_days)
        SharePermission.objects.update_or_create(
            dataset=access_request.dataset, shared_with_user=access_request.requester,
            defaults={"access_type": "download", "status": SharePermission.Status.ACTIVE, "expires_at": expires_at},
        )
        notify(
            user=access_request.requester, notification_type=Notification.NotificationType.DATASET_APPROVED,
            message=f'Your sharing request for "{access_request.dataset.title}" was approved.',
            dataset=access_request.dataset,
        )
        _send_share_email(access_request.requester_email, access_request.dataset, claim_token=None)
    else:
        access_request.claim_token = generate_invitation_token()
        access_request.claim_token_expires_at = timezone.now() + timedelta(days=CLAIM_EXPIRY_DAYS)
        _send_share_email(access_request.requester_email, access_request.dataset, claim_token=access_request.claim_token)

    if access_request.purpose_type == DatasetAccessRequest.PurposeType.EDIT:
        Dataset.objects.filter(id=access_request.dataset_id).update(edit_in_progress_notice=True)

    access_request.save(update_fields=["status", "resolved_at", "claim_token", "claim_token_expires_at"])


def _reject(access_request):
    access_request.status = DatasetAccessRequest.Status.REJECTED
    access_request.resolved_at = timezone.now()
    access_request.save(update_fields=["status", "resolved_at"])

    if access_request.requester_id:
        notify(
            user=access_request.requester, notification_type=Notification.NotificationType.REVISION_REJECTED,
            message=f'Your sharing request for "{access_request.dataset.title}" was declined.',
            dataset=access_request.dataset,
        )


def _send_share_email(email, dataset, claim_token):
    if claim_token:
        link = f"{dj_settings.FRONTEND_URL}/claim-access/{claim_token}"
        body = (
            f'You\'ve been granted access to "{dataset.title}" on ORDP.\n\n'
            f"You don't have an account yet — sign up with this email address, then visit "
            f"the link below to activate your access:\n{link}\n\n"
            f"This link expires in {CLAIM_EXPIRY_DAYS} days."
        )
    else:
        link = f"{dj_settings.FRONTEND_URL}/datasets/{dataset.id}"
        body = f'You\'ve been granted access to "{dataset.title}" on ORDP.\n\nView it here: {link}'

    send_mail(
        subject=f'You now have access to "{dataset.title}"',
        message=body, from_email=dj_settings.DEFAULT_FROM_EMAIL, recipient_list=[email],
    )


def claim_share_access(token, user):
    try:
        access_request = DatasetAccessRequest.objects.select_related("dataset").get(claim_token=token)
    except DatasetAccessRequest.DoesNotExist:
        raise ValueError("This access link is invalid.")

    if access_request.status != DatasetAccessRequest.Status.APPROVED:
        raise ValueError("This access grant is no longer valid.")
    if access_request.requester_id:
        raise ValueError("This access has already been claimed.")
    if access_request.claim_token_expires_at and access_request.claim_token_expires_at <= timezone.now():
        raise ValueError("This access link has expired.")
    if user.email.lower() != access_request.requester_email.lower():
        raise ValueError("This access grant was issued to a different email address.")

    expires_at = None
    if access_request.requested_duration_days:
        expires_at = timezone.now() + timedelta(days=access_request.requested_duration_days)
    SharePermission.objects.update_or_create(
        dataset=access_request.dataset, shared_with_user=user,
        defaults={"access_type": "download", "status": SharePermission.Status.ACTIVE, "expires_at": expires_at},
    )
    access_request.requester = user
    access_request.claim_token = None
    access_request.claim_token_expires_at = None
    access_request.save(update_fields=["requester", "claim_token", "claim_token_expires_at"])
    return access_request.dataset


def revoke_share_permission(permission, revoked_by):
    permission.status = SharePermission.Status.REVOKED
    permission.revoked_at = timezone.now()
    permission.revoked_by = revoked_by
    permission.save(update_fields=["status", "revoked_at", "revoked_by"])
    notify(
        user=permission.shared_with_user, notification_type=Notification.NotificationType.REVISION_REJECTED,
        message=f'Your access to "{permission.dataset.title}" has been revoked.',
        dataset=permission.dataset,
    )