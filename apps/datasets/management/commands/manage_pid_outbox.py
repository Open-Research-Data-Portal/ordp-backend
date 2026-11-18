import uuid

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.core.exceptions import ValidationError

from apps.datasets.models import OutboxEvent
from apps.datasets.services.pid import (
    clear_permanent_failure_and_retry,
    retry_failed_outbox_event,
)
from apps.datasets.services.pid_safety import sanitize_pid_text


class Command(BaseCommand):
    help = "Inspect PID outbox failures and explicitly retry eligible events."

    def add_arguments(self, parser):
        parser.add_argument("--status", choices=[choice for choice, _ in OutboxEvent.Status.choices])
        parser.add_argument("--limit", type=int, default=100)
        parser.add_argument("--retry-event", help="UUID of one failed event to retry.")
        parser.add_argument(
            "--override-permanent", action="store_true",
            help="Explicitly authorize one retry of a permanent or retry-exhausted event after remediation.",
        )
        parser.add_argument("--reason", default="")

    def handle(self, *args, **options):
        if options["retry_event"]:
            if not getattr(settings, "ORDP_DOI_REGISTRATION_ENABLED", False):
                raise CommandError("DOI registration is disabled; no event was requeued.")
            try:
                event_id = uuid.UUID(options["retry_event"])
                if options["override_permanent"]:
                    event = clear_permanent_failure_and_retry(
                        event_id, reason=options["reason"]
                    )
                else:
                    event = retry_failed_outbox_event(event_id)
            except (ValueError, OutboxEvent.DoesNotExist, ValidationError) as exc:
                raise CommandError(str(exc)) from exc
            self.stdout.write(self.style.SUCCESS(f"Requeued event {event.pk}."))
            return

        qs = OutboxEvent.objects.all().order_by("-updated_at")
        if options["status"]:
            qs = qs.filter(status=options["status"])
        if options["limit"] < 1:
            raise CommandError("--limit must be positive.")
        rows = list(qs[:options["limit"]])
        if not rows:
            self.stdout.write("No matching PID outbox events.")
            return
        for event in rows:
            permanent = bool(event.payload.get("_permanent_failure"))
            self.stdout.write(
                f"{event.pk} status={event.status} retries={event.retry_count} "
                f"permanent={permanent} available_at={event.available_at.isoformat()} "
                f"error={sanitize_pid_text(event.last_error)[:300]}"
            )
