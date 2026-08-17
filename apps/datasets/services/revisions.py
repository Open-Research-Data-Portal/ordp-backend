from django.conf import settings
from django.contrib.auth import get_user_model
from django.utils import timezone
from apps.accounts.models import ActivityLog
from apps.notifications.services import notify
from apps.notifications.models import Notification
from ..models import DatasetFile, DatasetRevision, PendingContentUpdate

from ..models import DatasetFile, DatasetRevision, PendingContentUpdate, DatasetVersion
User = get_user_model()


def _apply_metadata(dataset, proposed_metadata):
    if not proposed_metadata:
        return
    metadata = dataset.metadata
    for field, change in proposed_metadata.items():
        setattr(metadata, field, change["new"])
    metadata.save()


def bump_version_and_notify(dataset):
   

    dataset.version += 1
    dataset.save(update_fields=["version"])

    downloader_ids = (
        ActivityLog.objects
        .filter(action="dataset_download", target_object=f"Dataset:{dataset.id}")
        .values_list("user_id", flat=True)
        .distinct()
    )
    for user in User.objects.filter(id__in=downloader_ids):
        notify(
            user=user, notification_type=Notification.NotificationType.NEW_VERSION_AVAILABLE,
            message=f'"{dataset.title}" has a new version (v{dataset.version}).', dataset=dataset,
            link_path=f"/datasets/{dataset.id}",
        )


def route_change(*, dataset, source, submitted_by, new_file_key, diff_percentage,
                  change_summary, proposed_metadata, approved_by_owner=None):
    if diff_percentage >= settings.VERSION_BUMP_THRESHOLD_PCT:
        update = PendingContentUpdate.objects.create(
            dataset=dataset, source=source, submitted_by=submitted_by,
            approved_by_owner=approved_by_owner, new_file_key=new_file_key,
            proposed_metadata=proposed_metadata, diff_percentage=diff_percentage,
            change_summary=change_summary,
        )
        for reviewer in User.objects.filter(profile__role__in=["checker", "admin"]):
            notify(
                user=reviewer, notification_type=Notification.NotificationType.CONTENT_UPDATE_PENDING,
                message=f'A significant content change to "{dataset.title}" awaits review.', dataset=dataset,
                link_path=f"/admin-panel/content-updates/{update.id}",
            )
        return {"status": "pending_review", "pending_update_id": update.id}

    DatasetFile.objects.filter(dataset=dataset).update(file_key=new_file_key)
    _apply_metadata(dataset, proposed_metadata)
    return {"status": "applied"}


def apply_revision(revision: DatasetRevision):
    result = route_change(
        dataset=revision.dataset, source=PendingContentUpdate.Source.REVISION,
        submitted_by=revision.submitted_by, new_file_key=revision.new_file_key,
        diff_percentage=revision.diff_percentage, change_summary=revision.change_summary,
        proposed_metadata=revision.proposed_metadata, approved_by_owner=revision.dataset.owner,
    )
    revision.triggered_version_bump = (
        result["status"] == "applied" and revision.diff_percentage >= settings.VERSION_BUMP_THRESHOLD_PCT
    )
    revision.status = DatasetRevision.Status.APPROVED
    revision.save()
    return result

def _record_version(dataset, file_key, source, changed_by, change_summary, diff_percentage):
    next_number = DatasetVersion.objects.filter(dataset=dataset).count() + 1
    return DatasetVersion.objects.create(
        dataset=dataset, version_number=next_number, file_key=file_key, source=source,
        changed_by=changed_by, change_summary=change_summary, diff_percentage=diff_percentage,
    )

def decide_pending_content_update(update: PendingContentUpdate, decision, reviewer, reason=""):
    update.reviewed_by = reviewer
    update.decided_at = timezone.now()

    if decision == "approve":
        DatasetFile.objects.filter(dataset=update.dataset).update(file_key=update.new_file_key)
        _apply_metadata(update.dataset, update.proposed_metadata)
        bump_version_and_notify(update.dataset)
        DatasetVersion.objects.create(
            dataset=update.dataset, version_number=update.dataset.version, file_key=update.new_file_key,
            source=update.source, changed_by=update.submitted_by, change_summary=update.change_summary,
            diff_percentage=update.diff_percentage,
        )
        update.status = PendingContentUpdate.Status.APPROVED
        notify(
            user=update.submitted_by, notification_type=Notification.NotificationType.DATASET_APPROVED,
            message=f'The content update to "{update.dataset.title}" was approved and is now live.',
            dataset=update.dataset,
        )
    else:
        update.status = PendingContentUpdate.Status.REJECTED
        notify(
            user=update.submitted_by, notification_type=Notification.NotificationType.REVISION_REJECTED,
            message=f'The content update to "{update.dataset.title}" was rejected by review: {reason}',
            dataset=update.dataset, reason=reason,
        )
    update.save()
    return update