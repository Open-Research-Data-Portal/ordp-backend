from django.core.management.base import BaseCommand
from django.db import transaction

from apps.access_control.services import _system_grant_user
from apps.sharing.models import SharePermission


class Command(BaseCommand):
    help = "Backfill active SharePermission rows into V2 dataset.view and dataset.download grants."

    @transaction.atomic
    def handle(self, *args, **options):
        created = 0
        skipped = 0
        for share in SharePermission.objects.select_related("shared_with_user", "dataset").all():
            if not share.is_active_grant() or not share.shared_with_user_id:
                skipped += 1
                continue
            for code in ("dataset.view", "dataset.download"):
                _, was_created = _system_grant_user(
                    share.shared_with_user, code, "dataset", share.dataset_id,
                    expires_at=share.expires_at, actor=share.revoked_by,
                )
                if was_created:
                    created += 1
                else:
                    skipped += 1
        self.stdout.write(self.style.SUCCESS(
            f"Share migration complete. grants_created={created} skipped={skipped}"
        ))
