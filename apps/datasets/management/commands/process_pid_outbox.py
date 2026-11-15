import uuid
import time

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.datasets.services.pid import (
    DEFAULT_LEASE_SECONDS,
    claim_next_outbox_event,
    process_claimed_event,
)


class Command(BaseCommand):
    help = "Process queued persistent-identifier registration events."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=100)
        parser.add_argument("--lease-seconds", type=int, default=DEFAULT_LEASE_SECONDS)
        parser.add_argument(
            "--continuous", action="store_true",
            help="Keep polling for new work; intended for a dedicated background worker.",
        )
        parser.add_argument("--poll-seconds", type=float, default=10.0)

    def handle(self, *args, **options):
        limit = options["limit"]
        lease_seconds = options["lease_seconds"]
        continuous = options["continuous"]
        poll_seconds = options["poll_seconds"]
        if poll_seconds <= 0:
            raise CommandError("--poll-seconds must be greater than zero.")
        if limit < 1:
            raise CommandError("--limit must be a positive integer.")
        if lease_seconds < 1:
            raise CommandError("--lease-seconds must be a positive integer.")
        if not getattr(settings, "ORDP_DOI_REGISTRATION_ENABLED", False):
            self.stdout.write(
                self.style.WARNING(
                    "DOI registration is disabled; no outbox events were claimed."
                )
            )
            return

        worker_id = uuid.uuid4()
        while True:
            outcomes = {}
            processed = 0
            while processed < limit:
                claim = claim_next_outbox_event(
                    worker_id=worker_id,
                    lease_seconds=lease_seconds,
                )
                if claim is None:
                    break
                event_id, claimed_by = claim
                outcome = process_claimed_event(event_id, claimed_by)
                outcomes[outcome] = outcomes.get(outcome, 0) + 1
                processed += 1

            summary = ", ".join(
                f"{status}={count}" for status, count in sorted(outcomes.items())
            ) or "no eligible events"
            self.stdout.write(f"Processed {processed} event(s): {summary}.")
            if not continuous:
                break
            if processed == 0:
                time.sleep(poll_seconds)
