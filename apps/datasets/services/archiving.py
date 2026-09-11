from django.contrib.auth import get_user_model
from django.utils import timezone

from apps.notifications.services import notify
from apps.notifications.models import Notification
from apps.admin_panel.models import (
    DatasetArchiveRequest, ArchiveRequestVote,
    DatasetUnarchiveRequest, 
)

User = get_user_model()
MIN_REVIEWER_QUORUM = 3


def _quorum():
    total_reviewers = User.objects.filter(profile__roles__role__in=["reviewer", "admin"]).distinct().count()
    return min(MIN_REVIEWER_QUORUM, total_reviewers) or 1


def _notify_committee(dataset, requester, notification_type, message, link_path):
    for reviewer in User.objects.filter(profile__roles__role__in=["reviewer", "admin"]).distinct():
        if reviewer != requester:
            notify(user=reviewer, notification_type=notification_type, message=message,
                   dataset=dataset, link_path=link_path)


def request_archive(dataset, owner, reason_category, reason):
    archive_request = DatasetArchiveRequest.objects.create(
        dataset=dataset, dataset_title=dataset.title, requested_by=owner,
        reason_category=reason_category, reason=reason,
    
    )
    _notify_committee(
        dataset, owner, Notification.NotificationType.ARCHIVE_REQUESTED,
        f'{owner.profile.full_name} requested to archive "{dataset.title}".',
        f"/admin-panel/archive-requests/{archive_request.id}",
    )
    return archive_request


def request_unarchive(dataset, requester, intended_use, reason):
    unarchive_request = DatasetUnarchiveRequest.objects.create(
        dataset=dataset, dataset_title=dataset.title, requested_by=requester,
        intended_use=intended_use, reason=reason,
    )
    # Admin-only decide now — notify admins, not the reviewer committee.
    for admin_user in User.objects.filter(profile__roles__role="admin").distinct():
        if admin_user != requester:
            notify(
                user=admin_user, notification_type=Notification.NotificationType.UNARCHIVE_REQUESTED,
                message=f'{requester.profile.full_name} requested to restore "{dataset.title}" from the archive.',
                dataset=dataset, link_path=f"/admin-panel/unarchive-requests/{unarchive_request.id}",
            )
    return unarchive_request


def decide_unarchive_request(unarchive_request: DatasetUnarchiveRequest, admin_user, decision):
    """Single-admin decide — no quorum, no vote table."""
    if unarchive_request.status != DatasetUnarchiveRequest.Status.PENDING:
        return {"status": unarchive_request.status}

    unarchive_request.decided_by = admin_user
    unarchive_request.resolved_at = timezone.now()

    if decision == "approve":
        unarchive_request.status = DatasetUnarchiveRequest.Status.APPROVED
        unarchive_request.save(update_fields=["status", "decided_by", "resolved_at"])

        dataset = unarchive_request.dataset
        dataset.is_archived = False
        dataset.archived_at = None
        dataset.save(update_fields=["is_archived", "archived_at"])

        notify(
            user=dataset.owner, notification_type=Notification.NotificationType.DATASET_RESTORED,
            message=f'"{dataset.title}" has been restored and is visible again.', dataset=dataset,
        )
        return {"status": "approved"}

    unarchive_request.status = DatasetUnarchiveRequest.Status.REJECTED
    unarchive_request.save(update_fields=["status", "decided_by", "resolved_at"])

    notify(
        user=unarchive_request.requested_by, notification_type=Notification.NotificationType.UNARCHIVE_REJECTED,
        message=f'Your request to restore "{unarchive_request.dataset.title}" was declined.',
        dataset=unarchive_request.dataset,
    )
    return {"status": "rejected"}


def resolve_archive_request_votes(archive_request: DatasetArchiveRequest):
    if archive_request.status != DatasetArchiveRequest.Status.PENDING:
        return {"status": archive_request.status}

    quorum = _quorum()
    approve_votes = archive_request.votes.filter(vote="approve").count()
    reject_votes = archive_request.votes.filter(vote="reject").count()
    votes_cast = approve_votes + reject_votes

    if votes_cast < quorum:
        return {"status": "pending", "approve_votes": approve_votes, "reject_votes": reject_votes, "quorum": quorum}

    if approve_votes > reject_votes:
        archive_request.status = DatasetArchiveRequest.Status.APPROVED
        archive_request.resolved_at = timezone.now()
        archive_request.save(update_fields=["status", "resolved_at"])

        dataset = archive_request.dataset
        dataset.is_archived = True
        dataset.archived_at = timezone.now()
        dataset.save(update_fields=["is_archived", "archived_at"])

        notify(
            user=archive_request.requested_by, notification_type=Notification.NotificationType.DATASET_ARCHIVED,
            message=f'"{dataset.title}" has been archived.', dataset=dataset,
        )
        return {"status": "approved", "approve_votes": approve_votes, "reject_votes": reject_votes}

    if reject_votes > approve_votes:
        archive_request.status = DatasetArchiveRequest.Status.REJECTED
        archive_request.resolved_at = timezone.now()
        archive_request.save(update_fields=["status", "resolved_at"])

        notify(
            user=archive_request.requested_by, notification_type=Notification.NotificationType.ARCHIVE_REJECTED,
            message=f'Your request to archive "{archive_request.dataset.title}" was declined.',
            dataset=archive_request.dataset,
        )
        return {"status": "rejected", "approve_votes": approve_votes, "reject_votes": reject_votes}

    return {"status": "pending", "approve_votes": approve_votes, "reject_votes": reject_votes, "quorum": quorum}


