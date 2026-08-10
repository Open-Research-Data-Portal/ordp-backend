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
    if is_researcher and Contributor.objects.filter(dataset=dataset, user=user).exists():
        return True
    return False


def user_can_access_dataset(user, dataset):
    """Broader check used for version-history / metadata reads — download+share+
    reviewer+existing grant."""
    profile = getattr(user, "profile", None)
    is_reviewer = bool(profile and profile.has_role("checker", "admin"))
    return (
        user_can_freely_download(user, dataset)
        or is_reviewer
        or SharePermission.objects.filter(dataset=dataset, shared_with_user=user).exists()
    )


def resolve_access_request_votes(access_request: DatasetAccessRequest):
    """Called after every vote. Resolves once a real majority is reached among
    reviewers who actually voted, gated by a minimum quorum so one vote can't decide it."""
    total_reviewers = User.objects.filter(profile__roles__role__in=["checker", "admin"]).distinct().count()
    quorum = min(MIN_REVIEWER_QUORUM, total_reviewers) or 1

    approve_votes = access_request.votes.filter(vote="approve").count()
    reject_votes = access_request.votes.filter(vote="reject").count()
    votes_cast = approve_votes + reject_votes

    if votes_cast < quorum:
        return {"status": "pending", "approve_votes": approve_votes, "reject_votes": reject_votes, "quorum": quorum}

    if approve_votes > reject_votes:
        _approve(access_request)
        return {"status": "approved", "approve_votes": approve_votes, "reject_votes": reject_votes}

    if reject_votes > approve_votes:
        _reject(access_request)
        return {"status": "rejected", "approve_votes": approve_votes, "reject_votes": reject_votes}

    return {"status": "pending", "approve_votes": approve_votes, "reject_votes": reject_votes, "quorum": quorum}


def _approve(access_request):
    access_request.status = DatasetAccessRequest.Status.APPROVED
    access_request.resolved_at = timezone.now()
    access_request.save(update_fields=["status", "resolved_at"])

    SharePermission.objects.get_or_create(
        dataset=access_request.dataset, shared_with_user=access_request.requester,
        defaults={"access_type": "download"},
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
        message=f'Your sharing request for "{access_request.dataset.title}" was declined by the review committee.',
        dataset=access_request.dataset,
    )