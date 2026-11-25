from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from apps.access_control.services import _system_grant_user
from apps.sharing.models import DatasetAccessRequest


class Command(BaseCommand):
    help = (
        "Drain pending legacy DatasetAccessRequest rows: approved invites keep claim tokens; "
        "pending/rejected are expired or left for claim-only approved rows."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--approve-pending",
            action="store_true",
            help="Convert pending requests with a requester into V2 view/download grants.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        granted = 0
        expired = 0
        now = timezone.now()
        pending = DatasetAccessRequest.objects.filter(status=DatasetAccessRequest.Status.PENDING)
        if options["approve_pending"]:
            for row in pending.select_related("requester", "dataset"):
                if not row.requester_id:
                    row.status = DatasetAccessRequest.Status.REJECTED
                    row.resolved_at = now
                    row.save(update_fields=["status", "resolved_at"])
                    expired += 1
                    continue
                expires_at = None
                if row.requested_duration_days:
                    expires_at = now + timedelta(days=row.requested_duration_days)
                for code in ("dataset.view", "dataset.download"):
                    _system_grant_user(
                        row.requester, code, "dataset", row.dataset_id,
                        expires_at=expires_at, actor=row.shared_by,
                    )
                    granted += 1
                row.status = DatasetAccessRequest.Status.APPROVED
                row.resolved_at = now
                row.save(update_fields=["status", "resolved_at"])
        else:
            count = pending.update(status=DatasetAccessRequest.Status.REJECTED, resolved_at=now)
            expired += count
        self.stdout.write(self.style.SUCCESS(
            f"Legacy request drain complete. grants={granted} closed={expired}"
        ))
