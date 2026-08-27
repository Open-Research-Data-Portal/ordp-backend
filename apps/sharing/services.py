from datetime import timedelta

from django.contrib.auth import get_user_model
from django.utils import timezone

from apps.datasets.models import Dataset, Contributor
from apps.notifications.services import notify
from apps.notifications.models import Notification
from .models import SharePermission, DatasetAccessRequest

User = get_user_model()

MIN_REVIEWER_QUORUM = 3


def user_can_freely_download(user, dataset):
    profile = getattr(user, "profile", None)
    is_researcher = bool(profile and profile.has_role("researcher", "admin"))
    if dataset.is_owned_by(user):
        return True
    if dataset.visibility in (Dataset.Visibility.PUBLIC, Dataset.Visibility.INSTITUTIONAL):
        return True
    if is_researcher and Contributor.objects.filter(dataset=dataset, user=user).exists():
        return True
    return False


def user_can_access_dataset(user, dataset):
    """Broader check used for version-history / metadata reads / share-eligibility.
    Only counts an ACTIVE (non-expired, non-revoked) SharePermission."""
    profile = getattr(user, "profile", None)
    is_reviewer = bool(profile and profile.has_role("reviewer", "admin"))
    if user_can_freely_download(user, dataset) or is_reviewer:
        return True
    permission = SharePermission.objects.filter(dataset=dataset, shared_with_user=user).first()
    return bool(permission and permission.is_active_grant())


def resolve_access_request_votes(access_request: DatasetAccessRequest):
    """Access is granted only when BOTH the committee reaches quorum'd majority
    approval AND the owner has separately approved. Either side rejecting
    resolves the whole request to rejected immediately."""
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
    access_request.save(update_fields=["status", "resolved_at"])

    expires_at = None
    if access_request.requested_duration_days:
        expires_at = timezone.now() + timedelta(days=access_request.requested_duration_days)

    SharePermission.objects.update_or_create(
        dataset=access_request.dataset, shared_with_user=access_request.requester,
        defaults={"access_type": "download", "status": SharePermission.Status.ACTIVE, "expires_at": expires_at},
    )
    if access_request.purpose_type == DatasetAccessRequest.PurposeType.EDIT:
        Dataset.objects.filter(id=access_request.dataset_id).update(edit_in_progress_notice=True)

    notify(
        user=access_request.requester, notification_type=Notification.NotificationType.DATASET_APPROVED,
        message=f'Your sharing request for "{access_request.dataset.title}" was approved.',
        dataset=access_request.dataset,
    )


def _reject(access_request):
    access_request.status = DatasetAccessRequest.Status.REJECTED
    access_request.resolved_at = timezone.now()
    access_request.save(update_fields=["status", "resolved_at"])

    notify(
        user=access_request.requester, notification_type=Notification.NotificationType.REVISION_REJECTED,
        message=f'Your sharing request for "{access_request.dataset.title}" was declined.',
        dataset=access_request.dataset,
    )


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