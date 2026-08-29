from django.contrib.auth import get_user_model
from django.utils import timezone

from apps.notifications.services import notify
from apps.notifications.models import Notification
from .models import DatasetDeletionRequest, DeletionRequestVote

User = get_user_model()

MIN_REVIEWER_QUORUM = 3


def resolve_deletion_request_votes(deletion_request: DatasetDeletionRequest):
    """Same majority + quorum pattern as sharing's access-request votes. Approval
    here only flips status to APPROVED — it does NOT delete anything. An admin
    still has to call execute_hard_delete separately."""
    total_reviewers = User.objects.filter(profile__roles__role__in=["reviewer", "admin"]).distinct().count()
    quorum = min(MIN_REVIEWER_QUORUM, total_reviewers) or 1

    approve_votes = deletion_request.votes.filter(vote="approve").count()
    reject_votes = deletion_request.votes.filter(vote="reject").count()
    votes_cast = approve_votes + reject_votes

    if votes_cast < quorum:
        return {"status": "pending", "approve_votes": approve_votes, "reject_votes": reject_votes, "quorum": quorum}

    if approve_votes > reject_votes:
        deletion_request.status = DatasetDeletionRequest.Status.APPROVED
        deletion_request.resolved_at = timezone.now()
        deletion_request.save(update_fields=["status", "resolved_at"])
        for admin_user in User.objects.filter(profile__roles__role="admin").distinct():
            notify(
                user=admin_user, notification_type=Notification.NotificationType.DATASET_APPROVED,
                message=f'Reviewer committee approved permanent deletion of "{deletion_request.dataset.title}". Action required.',
                dataset=deletion_request.dataset,
                link_path=f"/admin-panel/deletion-requests/{deletion_request.id}",
            )
        return {"status": "approved", "approve_votes": approve_votes, "reject_votes": reject_votes}

    if reject_votes > approve_votes:
        deletion_request.status = DatasetDeletionRequest.Status.REJECTED
        deletion_request.resolved_at = timezone.now()
        deletion_request.save(update_fields=["status", "resolved_at"])
        return {"status": "rejected", "approve_votes": approve_votes, "reject_votes": reject_votes}

    return {"status": "pending", "approve_votes": approve_votes, "reject_votes": reject_votes, "quorum": quorum}


def execute_hard_delete(deletion_request, admin_user):
    """Actually destroys the dataset. Only call this behind an IsAdminOnly view,
    and only once status == APPROVED. Saves the request record BEFORE deleting
    the dataset, so the audit trail survives — dataset FK is SET_NULL, not CASCADE."""
    dataset = deletion_request.dataset
    title = dataset.title

    deletion_request.status = DatasetDeletionRequest.Status.EXECUTED
    deletion_request.executed_at = timezone.now()
    deletion_request.executed_by = admin_user
    deletion_request.dataset_title = title
    deletion_request.save(update_fields=["status", "executed_at", "executed_by", "dataset_title"])

    dataset.delete()
    return title