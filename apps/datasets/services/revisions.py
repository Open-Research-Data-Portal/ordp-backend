from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.mail import send_mail
from django.utils import timezone

from apps.accounts.models import ActivityLog
from apps.notifications.services import notify
from apps.notifications.models import Notification
from ..models import (
    DatasetFile, PendingContentUpdate, DatasetVersion,
    RevisionRequest, RevisionRequestVote, PendingContentUpdateVote, DatasetWatcher, Contributor,
)

User = get_user_model()
MIN_REVIEWER_QUORUM = 3
RECEIVED_DOWNLOAD_ACTIONS = ["owner_download", "contributor_download", "dataset_download", "reviewer_download"]


def _apply_metadata(dataset, proposed_metadata):
    if not proposed_metadata:
        return
    metadata = dataset.metadata
    for field, change in proposed_metadata.items():
        setattr(metadata, field, change["new"])
    metadata.save()


def _notify_regular_recipients(dataset, exclude_user, message):
    """Owner, co-authors/contributors, everyone who's downloaded — NOT watchers.
    Only sent for a real new version (major, committee-approved change) —
    minor changes do not reach these people."""
    recipients = {dataset.owner}
    contributor_user_ids = Contributor.objects.filter(dataset=dataset, user__isnull=False).values_list("user_id", flat=True)
    recipients.update(User.objects.filter(id__in=contributor_user_ids))

    downloader_ids = (
        ActivityLog.objects.filter(action__in=RECEIVED_DOWNLOAD_ACTIONS, target_object=f"Dataset:{dataset.id}")
        .values_list("user_id", flat=True).distinct()
    )
    recipients.update(User.objects.filter(id__in=downloader_ids))
    recipients.discard(exclude_user)

    for user in recipients:
        notify(
            user=user, notification_type=Notification.NotificationType.NEW_VERSION_AVAILABLE,
            message=message, dataset=dataset, link_path=f"/datasets/{dataset.id}",
        )


def _notify_watchers(dataset, exclude_user, message):
    """Anyone who clicked 'Notify me' — gets told about BOTH minor changes and
    real new versions, since they specifically asked to be kept informed
    regardless of which kind resolves. One-shot: cleared after notifying."""
    watchers = [w.user for w in DatasetWatcher.objects.filter(dataset=dataset).select_related("user")
                if w.user_id != exclude_user.id]
    for user in watchers:
        notify(
            user=user, notification_type=Notification.NotificationType.NEW_VERSION_AVAILABLE,
            message=message, dataset=dataset, link_path=f"/datasets/{dataset.id}",
        )
    DatasetWatcher.objects.filter(dataset=dataset).delete()


def bump_version_and_notify(dataset, changed_by):
    dataset.version += 1
    dataset.save(update_fields=["version"])
    message = f'"{dataset.title}" has a new version (v{dataset.version}).'
    _notify_regular_recipients(dataset, exclude_user=changed_by, message=message)
    _notify_watchers(dataset, exclude_user=changed_by, message=message)


def route_change(*, dataset, source, submitted_by, new_file_key, diff_percentage,
                  change_summary, proposed_metadata, approved_by_owner=None):
    if diff_percentage >= settings.VERSION_BUMP_THRESHOLD_PCT:
        update = PendingContentUpdate.objects.create(
            dataset=dataset, source=source, submitted_by=submitted_by,
            approved_by_owner=approved_by_owner, new_file_key=new_file_key,
            proposed_metadata=proposed_metadata, diff_percentage=diff_percentage,
            change_summary=change_summary,
        )
        for reviewer in User.objects.filter(profile__roles__role__in=["checker", "admin"]).distinct():
            notify(
                user=reviewer, notification_type=Notification.NotificationType.CONTENT_UPDATE_PENDING,
                message=f'A significant content change to "{dataset.title}" awaits review.', dataset=dataset,
                link_path=f"/admin-panel/content-updates/{update.id}",
            )
        return {"status": "pending_review", "pending_update_id": update.id}


    DatasetFile.objects.filter(dataset=dataset).update(file_key=new_file_key)
    _apply_metadata(dataset, proposed_metadata)
    _notify_watchers(
        dataset, exclude_user=submitted_by,
        message=f'"{dataset.title}" was updated with a minor change.',
    )
    return {"status": "applied"}



def request_revision_permission(dataset, requester, reason):
    request = RevisionRequest.objects.create(dataset=dataset, requester=requester, reason=reason)
    for reviewer in User.objects.filter(profile__roles__role__in=["checker", "admin"]).distinct():
        notify(
            user=reviewer, notification_type=Notification.NotificationType.CONTENT_UPDATE_PENDING,
            message=f'{requester.profile.full_name} is requesting permission to propose changes to "{dataset.title}".',
            dataset=dataset, link_path=f"/admin-panel/revision-requests/{request.id}",
        )
    return request


def resolve_revision_request_votes(request: RevisionRequest):
    if request.status != RevisionRequest.Status.PENDING:
        return {"status": request.status}

    total_reviewers = User.objects.filter(profile__roles__role__in=["checker", "admin"]).distinct().count()
    quorum = min(MIN_REVIEWER_QUORUM, total_reviewers) or 1
    approve_votes = request.votes.filter(vote="approve").count()
    reject_votes = request.votes.filter(vote="reject").count()
    votes_cast = approve_votes + reject_votes

    if votes_cast < quorum:
        return {"status": "pending", "approve_votes": approve_votes, "reject_votes": reject_votes, "quorum": quorum}

    if approve_votes > reject_votes:
        request.status = RevisionRequest.Status.APPROVED
        request.resolved_at = timezone.now()
        request.save(update_fields=["status", "resolved_at"])
        link = f"{settings.FRONTEND_URL}/datasets/{request.dataset.id}/modify?token={request.token}"
        send_mail(
            subject=f'Your request to modify "{request.dataset.title}" was approved',
            message=(
                f'The reviewer committee approved your request. You can now propose a change: {link}\n\n'
                f'(You can also just navigate to the dataset directly and choose "Modify" — the link is a shortcut, not required.)'
            ),
            from_email=settings.DEFAULT_FROM_EMAIL, recipient_list=[request.requester.email],
        )
        notify(
            user=request.requester, notification_type=Notification.NotificationType.DATASET_APPROVED,
            message=f'You may now propose a change to "{request.dataset.title}".',
            dataset=request.dataset, link_path=f"/datasets/{request.dataset.id}/modify",
        )
        return {"status": "approved", "approve_votes": approve_votes, "reject_votes": reject_votes}

    if reject_votes > approve_votes:
        request.status = RevisionRequest.Status.REJECTED
        request.resolved_at = timezone.now()
        request.save(update_fields=["status", "resolved_at"])
        notify(
            user=request.requester, notification_type=Notification.NotificationType.REVISION_REJECTED,
            message=f'Your request to modify "{request.dataset.title}" was declined.',
            dataset=request.dataset,
        )
        return {"status": "rejected", "approve_votes": approve_votes, "reject_votes": reject_votes}

    return {"status": "pending", "approve_votes": approve_votes, "reject_votes": reject_votes, "quorum": quorum}


def has_revision_permission(dataset, user):
    return RevisionRequest.objects.filter(
        dataset=dataset, requester=user, status=RevisionRequest.Status.APPROVED, used=False
    ).exists()


def consume_revision_permission(dataset, user):
    RevisionRequest.objects.filter(
        dataset=dataset, requester=user, status=RevisionRequest.Status.APPROVED, used=False
    ).update(used=True)




def resolve_content_update_votes(update: PendingContentUpdate):
    if update.status != PendingContentUpdate.Status.PENDING:
        return {"status": update.status}

    total_reviewers = User.objects.filter(profile__roles__role__in=["checker", "admin"]).distinct().count()
    quorum = min(MIN_REVIEWER_QUORUM, total_reviewers) or 1
    approve_votes = update.votes.filter(vote="approve").count()
    reject_votes = update.votes.filter(vote="reject").count()
    votes_cast = approve_votes + reject_votes

    if votes_cast < quorum:
        return {"status": "pending", "approve_votes": approve_votes, "reject_votes": reject_votes, "quorum": quorum}

    if approve_votes > reject_votes:
        _apply_pending_content_update(update)
        return {"status": "approved", "approve_votes": approve_votes, "reject_votes": reject_votes}

    if reject_votes > approve_votes:
        update.status = PendingContentUpdate.Status.REJECTED
        update.decided_at = timezone.now()
        update.save(update_fields=["status", "decided_at"])
        notify(
            user=update.submitted_by, notification_type=Notification.NotificationType.REVISION_REJECTED,
            message=f'The reviewer committee declined the content update to "{update.dataset.title}".',
            dataset=update.dataset,
        )
        return {"status": "rejected", "approve_votes": approve_votes, "reject_votes": reject_votes}

    return {"status": "pending", "approve_votes": approve_votes, "reject_votes": reject_votes, "quorum": quorum}


def _apply_pending_content_update(update):
    DatasetFile.objects.filter(dataset=update.dataset).update(file_key=update.new_file_key)
    _apply_metadata(update.dataset, update.proposed_metadata)
    bump_version_and_notify(update.dataset, changed_by=update.submitted_by)
    DatasetVersion.objects.create(
        dataset=update.dataset, version_number=update.dataset.version, file_key=update.new_file_key,
        source=update.source, changed_by=update.submitted_by, change_summary=update.change_summary,
        diff_percentage=update.diff_percentage,
    )
    update.status = PendingContentUpdate.Status.APPROVED
    update.decided_at = timezone.now()
    update.save(update_fields=["status", "decided_at"])
    notify(
        user=update.submitted_by, notification_type=Notification.NotificationType.DATASET_APPROVED,
        message=f'Your content update to "{update.dataset.title}" was approved and is now live.',
        dataset=update.dataset,
    )