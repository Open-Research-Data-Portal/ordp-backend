from datetime import timedelta

from django.conf import settings as dj_settings
from django.contrib.auth import get_user_model
from django.core.mail import send_mail
from django.utils import timezone

from apps.datasets.models import Dataset, Contributor
from apps.datasets.models import generate_invitation_token
from apps.notifications.services import notify
from apps.notifications.models import Notification
from .models import SharePermission, DatasetAccessRequest, UsabilityFormResponse

User = get_user_model()

MIN_REVIEWER_QUORUM = 3
CLAIM_EXPIRY_DAYS = 14


def user_meets_access_requirements(user):
    """The one profile check every download/claim funnels through — no
    exceptions for role. A no-op for the real dataset owner (already
    enforced at upload time), and a real gate for promoted co-owners,
    accepted-invitation contributors, and anyone whose profile has since
    gone incomplete."""
    if not user or not user.is_authenticated:
        return False
    profile = getattr(user, "profile", None)
    return bool(profile and profile.is_profile_complete())


def user_can_freely_download(user, dataset):
    """Owner, contributors, and admins only. Reviewers get no bypass here —
    they go through the same request flow as any other outside user."""
    if not user or not user.is_authenticated:
        return False

    if dataset.visibility == Dataset.Visibility.PRIVATE:
        return dataset.owner_id == user.id

    if dataset.owner_id == user.id:
        return True

    profile = getattr(user, "profile", None)
    if profile and profile.has_role("admin"):
        return True

    return Contributor.objects.filter(dataset=dataset, user=user).exists()


def user_can_access_dataset(user, dataset):
    """Broader check used for version-history / metadata reads / share-eligibility.
    Only counts an ACTIVE (non-expired, non-revoked) SharePermission."""
    if dataset.visibility == Dataset.Visibility.PRIVATE:
        return bool(user and user.is_authenticated and dataset.owner_id == user.id)

    profile = getattr(user, "profile", None)
    is_admin = bool(profile and profile.has_role("admin")) and dataset.visibility == Dataset.Visibility.PUBLIC
    if user_can_freely_download(user, dataset) or is_admin:
        return True
    if not user or not user.is_authenticated:
        return False
    permission = SharePermission.objects.filter(dataset=dataset, shared_with_user=user).first()
    return bool(permission and permission.is_active_grant())


def resolve_access_request_votes(access_request: DatasetAccessRequest):
    if access_request.status != DatasetAccessRequest.Status.PENDING:
        return {"status": access_request.status}

    if access_request.owner_decision == DatasetAccessRequest.OwnerDecision.REJECTED:
        _reject(access_request)
        return {"status": "rejected", "reason": "owner_declined"}

    if access_request.owner_decision != DatasetAccessRequest.OwnerDecision.APPROVED:
        return {"status": "pending", "owner_decision": access_request.owner_decision}

    total_reviewers = User.objects.filter(profile__roles__role="reviewer").distinct().count()
    quorum = min(MIN_REVIEWER_QUORUM, total_reviewers) or 1
    approve_votes = access_request.votes.filter(vote="approve").count()
    reject_votes = access_request.votes.filter(vote="reject").count()
    votes_cast = approve_votes + reject_votes

    if votes_cast >= quorum and reject_votes > approve_votes:
        _reject(access_request)
        return {"status": "rejected", "approve_votes": approve_votes, "reject_votes": reject_votes}

    if votes_cast >= quorum and approve_votes > reject_votes:
        _approve(access_request)
        return {"status": "approved", "approve_votes": approve_votes, "reject_votes": reject_votes}

    return {
        "status": "pending", "approve_votes": approve_votes, "reject_votes": reject_votes,
        "quorum": quorum, "owner_decision": access_request.owner_decision,
    }


def record_owner_decision(access_request, decision):
    if access_request.status != DatasetAccessRequest.Status.PENDING:
        raise ValueError("This request has already been resolved.")
    access_request.owner_decision = (
        DatasetAccessRequest.OwnerDecision.APPROVED if decision == "approve"
        else DatasetAccessRequest.OwnerDecision.REJECTED
    )
    access_request.save(update_fields=["owner_decision"])

    result = resolve_access_request_votes(access_request)

    if access_request.owner_decision == DatasetAccessRequest.OwnerDecision.APPROVED and result["status"] == "pending":
        for reviewer in User.objects.filter(profile__roles__role="reviewer").distinct():
            notify(
                user=reviewer, notification_type=Notification.NotificationType.ACCESS_REQUEST,
                message=(
                    f'The owner approved a sharing request for "{access_request.dataset.title}" '
                    f"— your committee review is needed."
                ),
                dataset=access_request.dataset, link_path=f"/admin-panel/access-requests/{access_request.id}",
            )
    return result


def _approve(access_request):
    """Approval never grants access directly anymore — it always issues a
    claim link. The one exception (instant public self-service) is handled
    inline in the view and never creates a DatasetAccessRequest at all, so
    it never reaches this function."""
    access_request.status = DatasetAccessRequest.Status.APPROVED
    access_request.resolved_at = timezone.now()
    access_request.claim_token = generate_invitation_token()
    access_request.claim_token_expires_at = timezone.now() + timedelta(days=CLAIM_EXPIRY_DAYS)

    if access_request.requester_id:
        notify(
            user=access_request.requester, notification_type=Notification.NotificationType.DATASET_APPROVED,
            message=f'Your access to "{access_request.dataset.title}" is ready — activate it to start downloading.',
            dataset=access_request.dataset,
        )

    _send_share_email(access_request.requester_email, access_request.dataset, access_request.claim_token)

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
    link = f"{dj_settings.FRONTEND_URL}/claim-access/{claim_token}"
    body = (
        f'You\'ve been granted access to "{dataset.title}" on ORDP.\n\n'
        f"Activate your access here: {link}\n\n"
        f"This link expires in {CLAIM_EXPIRY_DAYS} days."
    )
    send_mail(
        subject=f'You now have access to "{dataset.title}"',
        message=body, from_email=dj_settings.DEFAULT_FROM_EMAIL, recipient_list=[email],
    )


def claim_share_access(token, user, purpose=None):
    try:
        access_request = DatasetAccessRequest.objects.select_related("dataset").get(claim_token=token)
    except DatasetAccessRequest.DoesNotExist:
        raise ValueError("This access link is invalid.")

    if access_request.status != DatasetAccessRequest.Status.APPROVED:
        raise ValueError("This access grant is no longer valid.")
    if access_request.claim_token_expires_at and access_request.claim_token_expires_at <= timezone.now():
        raise ValueError("This access link has expired.")

    if access_request.requester_id:
        if access_request.requester_id != user.id:
            raise ValueError("This access grant was issued to a different account.")
    else:
        if user.email.lower() != access_request.requester_email.lower():
            raise ValueError("This access grant was issued to a different email address.")

    if not user_meets_access_requirements(user):
        raise ValueError("Please complete your profile before accessing this dataset.")

    is_delegated = access_request.shared_by_id is not None
    if is_delegated:
        purpose_text = (purpose or "").strip()
        if not purpose_text:
            raise ValueError("Please describe your purpose for accessing this dataset before continuing.")
        UsabilityFormResponse.objects.create(dataset=access_request.dataset, user=user, purpose=purpose_text)

    expires_at = None
    if access_request.requested_duration_days:
        expires_at = timezone.now() + timedelta(days=access_request.requested_duration_days)
    SharePermission.objects.update_or_create(
        dataset=access_request.dataset, shared_with_user=user,
        defaults={"access_type": "download", "status": SharePermission.Status.ACTIVE, "expires_at": expires_at},
    )

    update_fields = ["claim_token", "claim_token_expires_at"]
    if not access_request.requester_id:
        access_request.requester = user
        update_fields.append("requester")
    access_request.claim_token = None
    access_request.claim_token_expires_at = None
    access_request.save(update_fields=update_fields)
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