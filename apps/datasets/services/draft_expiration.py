from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from apps.accounts.models import ActivityLog
from apps.datasets.models import Dataset


DEFAULT_DRAFT_EXPIRATION_DAYS = 30


def draft_expiration_days():
    return int(getattr(settings, "DRAFT_EXPIRATION_DAYS", DEFAULT_DRAFT_EXPIRATION_DAYS))


def expired_drafts_qs(days=None):
    days = draft_expiration_days() if days is None else int(days)
    cutoff = timezone.now() - timedelta(days=days)
    return Dataset.objects.filter(status=Dataset.Status.DRAFT, is_active=True, updated_at__lt=cutoff)


def expire_inactive_drafts(days=None, *, actor=None, dry_run=False):
    drafts = list(expired_drafts_qs(days).select_related("owner", "owner__profile"))
    if dry_run:
        return {
            "expired_count": len(drafts),
            "deleted_count": 0,
            "drafts": [_serialize_draft(draft) for draft in drafts],
        }

    deleted = 0
    for draft in drafts:
        ActivityLog.objects.create(
            user=actor or draft.owner,
            action="draft_auto_deleted",
            target_object=f"Dataset:{draft.id}",
            ip_address="0.0.0.0",
            extra={"title": draft.title, "owner_id": draft.owner_id},
        )
        draft.delete()
        deleted += 1

    return {
        "expired_count": len(drafts),
        "deleted_count": deleted,
        "drafts": [_serialize_draft(draft) for draft in drafts],
    }


def _serialize_draft(draft):
    owner_profile = getattr(draft.owner, "profile", None)
    return {
        "id": str(draft.id),
        "title": draft.title,
        "owner_id": draft.owner_id,
        "owner": getattr(owner_profile, "full_name", "") or draft.owner.email,
        "updated_at": draft.updated_at,
        "created_at": draft.created_at,
    }
