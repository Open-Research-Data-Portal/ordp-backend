import uuid
import logging
from dataclasses import dataclass, field
from datetime import timedelta
from importlib import import_module

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models.expressions import RawSQL
from django.utils import timezone

from apps.datasets.models import (
    DOIRegistrationAttempt,
    Dataset,
    DatasetRelease,
    OutboxEvent,
    PIDRegistrationEvent,
    ResourceIdentifier,
    ResourceRelationship,
)
from apps.datasets.services.pid_safety import sanitize_pid_text


logger = logging.getLogger(__name__)

EVENT_REGISTER_IDENTIFIER = "pid.register_identifier"
MAX_OUTBOX_RETRIES = getattr(settings, "ORDP_MAX_OUTBOX_RETRIES", 8)  # New configurable max retries
MAX_REGISTRATION_ATTEMPTS = MAX_OUTBOX_RETRIES  # Backward compatibility
DEFAULT_LEASE_SECONDS = 300
MAX_BACKOFF_SECONDS = 6 * 60 * 60


class DOIProviderError(Exception):
    """Base error raised by a DOI registration provider."""


class TransientDOIProviderError(DOIProviderError):
    """A provider error that may succeed on a later attempt."""


class PermanentDOIProviderError(DOIProviderError):
    """A non-retryable provider or request error requiring operator action."""


@dataclass(frozen=True)
class RegistrationResult:
    identifier_value: str
    landing_page_url: str = ""
    response_summary: dict = field(default_factory=dict)


class DOIRegistrationProvider:
    """Provider boundary. Implementations must honor the supplied idempotency key."""

    def register(self, identifier, *, idempotency_key):
        raise NotImplementedError


class MockDOIProvider(DOIRegistrationProvider):
    """Test provider that returns only explicitly configured provider responses."""

    def __init__(self, responses=None, errors=None):
        self.responses = responses or {}
        self.errors = errors or {}

    def register(self, identifier, *, idempotency_key):
        if identifier.pk in self.errors:
            raise self.errors[identifier.pk]
        result = self.responses.get(identifier.pk)
        if result is None:
            raise DOIProviderError(
                "Mock provider has no configured response for this identifier."
            )
        return result


def get_doi_provider(provider_name=None):
    provider_class = getattr(settings, "ORDP_DOI_PROVIDER_CLASS", "")
    if provider_class:
        try:
            module_name, class_name = provider_class.rsplit(".", 1)
            provider = getattr(import_module(module_name), class_name)
            return provider()
        except Exception as exc:
            raise PermanentDOIProviderError(
                "The configured DOI provider could not be loaded."
            ) from exc
    provider_name = provider_name or getattr(settings, "ORDP_DOI_PROVIDER", "mock")
    if provider_name == "mock":
        return MockDOIProvider()
    raise PermanentDOIProviderError(f"DOI provider '{provider_name}' is not installed.")




def _ordp_landing_url(pid_value):
    from django.conf import settings
    base = (
        getattr(settings, "ORDP_PUBLIC_BASE_URL", "")
        or getattr(settings, "ORDP_DOMAIN", "")
        or getattr(settings, "SITE_URL", "")
    ).strip().rstrip("/")
    if base and not base.startswith(("https://", "http://")):
        base = f"https://{base}"
    return f"{base}/id/{pid_value}" if base else ""

def _audit_ordp_pid_assignment(identifier, resource_id):
    PIDRegistrationEvent.objects.create(
        sequence=RawSQL("nextval('datasets_pidregistrationevent_sequence_seq')", []),
        event_id=uuid.uuid4(),
        event_type="ordp.pid_assigned",
        from_status="",
        to_status=ResourceIdentifier.LifecycleStatus.ACTIVE,
        retry_count=0,
        detail=f"scheme=ordp; role={identifier.role}; resource_id={resource_id}; pid={identifier.identifier_value}",
    )


def ensure_ordp_concept_identifier(dataset, *, creation_source="publication"):
    """Ensure the one immutable ORDP Concept PID for a published dataset."""
    with transaction.atomic():
        locked_dataset = Dataset.objects.select_for_update().get(pk=dataset.pk)
        if locked_dataset.status != Dataset.Status.PUBLISHED:
            raise ValidationError("ORDP Concept PIDs can only be assigned to published datasets.")
        concept_value = f"ORDP-DS-{locked_dataset.pk.hex.upper()}"
        concept, created = ResourceIdentifier.objects.get_or_create(
            dataset_id=locked_dataset.pk,
            scheme=ResourceIdentifier.Scheme.ORDP,
            role=ResourceIdentifier.Role.CONCEPT,
            defaults={
                "identifier_value": concept_value,
                "registration_status": ResourceIdentifier.RegistrationStatus.NOT_REQUESTED,
                "landing_page_url": _ordp_landing_url(concept_value),
                "creation_source": creation_source,
            },
        )
        if created:
            _audit_ordp_pid_assignment(concept, locked_dataset.pk)
        return concept


def ensure_ordp_release_identifiers(release):
    """Always assign ORDP-owned Concept and Version PIDs, independent of DOI policy."""
    from django.db import transaction

    with transaction.atomic():
        dataset_id = DatasetRelease.objects.values_list("dataset_id", flat=True).get(pk=release.pk)
        dataset = Dataset.objects.select_for_update().get(pk=dataset_id)
        locked_release = DatasetRelease.objects.select_for_update(of=("self",)).select_related("base_release").get(pk=release.pk)
        if locked_release.status not in {DatasetRelease.Status.PUBLISHED, DatasetRelease.Status.WITHDRAWN}:
            raise ValidationError("ORDP PIDs can only be assigned to published releases.")
        version_value = f"ORDP-REL-{locked_release.pk.hex.upper()}"

        concept = ensure_ordp_concept_identifier(dataset)
        version, version_created = ResourceIdentifier.objects.get_or_create(
            release_id=locked_release.pk,
            scheme=ResourceIdentifier.Scheme.ORDP,
            role=ResourceIdentifier.Role.VERSION,
            defaults={
                "identifier_value": version_value,
                "registration_status": ResourceIdentifier.RegistrationStatus.NOT_REQUESTED,
                "landing_page_url": _ordp_landing_url(version_value),
                "creation_source": "publication",
            },
        )
        if version_created:
            _audit_ordp_pid_assignment(version, locked_release.pk)
        ResourceRelationship.objects.get_or_create(
            subject_identifier=concept,
            related_identifier=version,
            relationship_type=ResourceRelationship.Type.HAS_VERSION,
        )
        ResourceRelationship.objects.get_or_create(
            subject_identifier=version,
            related_identifier=concept,
            relationship_type=ResourceRelationship.Type.IS_VERSION_OF,
        )
        previous_release = locked_release.base_release
        if previous_release and previous_release.dataset_id == dataset.pk:
            previous_version = ResourceIdentifier.objects.filter(
                release=previous_release,
                scheme=ResourceIdentifier.Scheme.ORDP,
                role=ResourceIdentifier.Role.VERSION,
            ).first()
            if previous_version:
                ResourceRelationship.objects.get_or_create(
                    subject_identifier=version,
                    related_identifier=previous_version,
                    relationship_type=ResourceRelationship.Type.IS_NEW_VERSION_OF,
                )
                ResourceRelationship.objects.get_or_create(
                    subject_identifier=previous_version,
                    related_identifier=version,
                    relationship_type=ResourceRelationship.Type.IS_PREVIOUS_VERSION_OF,
                )
        return concept, version


def ensure_release_identifiers(release):
    """Create configured concept/version identifier records and durable work.

    Policy controls which DOI scopes are requested. The default preserves the
    agreed hybrid concept + version strategy; registration itself remains
    disabled until the institution enables a provider.
    """
    # ORDP identifiers are first-party identities and never depend on DOI settings.
    ordp_concept, ordp_version = ensure_ordp_release_identifiers(release)
    if release.status == DatasetRelease.Status.WITHDRAWN:
        return ordp_concept, ordp_version
    provider_name = getattr(settings, "ORDP_DOI_PROVIDER", "mock")
    policy = getattr(settings, "ORDP_DOI_REGISTRATION_POLICY", "dataset_and_release")
    valid_policies = {"dataset_only", "release_only", "dataset_and_release", "disabled"}
    if policy not in valid_policies:
        # Identifier configuration must never make an otherwise valid release
        # publication fail. Emit an operational error and leave identifiers
        # untouched until configuration is corrected and backfilled.
        logger.error(
            "Invalid ORDP_DOI_REGISTRATION_POLICY=%r; skipping PID creation. Valid values: %s",
            policy,
            ", ".join(sorted(valid_policies)),
        )
        return ordp_concept, ordp_version
    if policy == "disabled":
        return ordp_concept, ordp_version

    concept = version = None
    if policy in {"dataset_only", "dataset_and_release"}:
        concept, _ = ResourceIdentifier.objects.get_or_create(
            dataset_id=release.dataset_id,
            scheme=ResourceIdentifier.Scheme.DOI,
            role=ResourceIdentifier.Role.CONCEPT,
            defaults={
                "provider": provider_name,
                "registration_status": ResourceIdentifier.RegistrationStatus.PENDING,
            },
        )
    if policy in {"release_only", "dataset_and_release"}:
        version, _ = ResourceIdentifier.objects.get_or_create(
            release=release,
            scheme=ResourceIdentifier.Scheme.DOI,
            role=ResourceIdentifier.Role.VERSION,
            defaults={
                "provider": provider_name,
                "registration_status": ResourceIdentifier.RegistrationStatus.PENDING,
            },
        )

    for identifier in (concept, version):
        if identifier is None:
            continue
        changed_fields = []
        if identifier.provider in ("", "unconfigured") and (
            identifier.registration_status != ResourceIdentifier.RegistrationStatus.REGISTERED
        ):
            identifier.provider = provider_name
            changed_fields.append("provider")
        if identifier.registration_status == ResourceIdentifier.RegistrationStatus.NOT_REQUESTED:
            identifier.registration_status = ResourceIdentifier.RegistrationStatus.PENDING
            changed_fields.append("registration_status")
        if changed_fields:
            identifier.save(update_fields=[*changed_fields, "updated_at"])
        event, created = OutboxEvent.objects.get_or_create(
            idempotency_key=f"pid-register:{identifier.pk}",
            defaults={
                "event_type": EVENT_REGISTER_IDENTIFIER,
                "payload": {"identifier_id": str(identifier.pk)},
            },
        )
        if (
            not created
            and event.status == OutboxEvent.Status.CANCELLED
            and event.last_error.startswith("Cancelled by PID policy reconciliation")
            and identifier.lifecycle_status == ResourceIdentifier.LifecycleStatus.ACTIVE
            and identifier.registration_status == ResourceIdentifier.RegistrationStatus.PENDING
        ):
            event.status = OutboxEvent.Status.PENDING
            event.available_at = timezone.now()
            event.failed_at = None
            event.last_error = ""
            event.save(update_fields=["status", "available_at", "failed_at", "last_error", "updated_at"])

    if concept and version:
        ResourceRelationship.objects.get_or_create(
            subject_identifier=concept,
            related_identifier=version,
            relationship_type=ResourceRelationship.Type.HAS_VERSION,
        )
        ResourceRelationship.objects.get_or_create(
            subject_identifier=version,
            related_identifier=concept,
            relationship_type=ResourceRelationship.Type.IS_VERSION_OF,
        )

    if version:
        from django.db.models import Q
        previous_release = DatasetRelease.objects.filter(
            dataset_id=release.dataset_id,
            status__in=[DatasetRelease.Status.PUBLISHED, DatasetRelease.Status.WITHDRAWN],
        ).exclude(pk=release.pk).filter(
            Q(major__lt=release.major)
            | Q(major=release.major, minor__lt=release.minor)
            | Q(major=release.major, minor=release.minor, patch__lt=release.patch)
        ).order_by("-major", "-minor", "-patch").first()
        if previous_release:
            previous_version = ResourceIdentifier.objects.filter(
                release=previous_release,
                scheme=ResourceIdentifier.Scheme.DOI,
                role=ResourceIdentifier.Role.VERSION,
            ).first()
            if previous_version:
                ResourceRelationship.objects.get_or_create(
                    subject_identifier=version,
                    related_identifier=previous_version,
                    relationship_type=ResourceRelationship.Type.IS_NEW_VERSION_OF,
                )
                ResourceRelationship.objects.get_or_create(
                    subject_identifier=previous_version,
                    related_identifier=version,
                    relationship_type=ResourceRelationship.Type.IS_PREVIOUS_VERSION_OF,
                )
    return concept, version


def withdraw_release_identifiers(release):
    """Withdraw version identifiers without erasing provider registration history."""
    identifiers = ResourceIdentifier.objects.select_for_update().filter(
        release=release,
        role=ResourceIdentifier.Role.VERSION,
    )
    for identifier in identifiers:
        identifier.lifecycle_status = ResourceIdentifier.LifecycleStatus.WITHDRAWN
        if (
            identifier.scheme == ResourceIdentifier.Scheme.DOI
            and identifier.registration_status != ResourceIdentifier.RegistrationStatus.REGISTERED
        ):
            identifier.registration_status = ResourceIdentifier.RegistrationStatus.CANCELLED
        identifier.save(update_fields=[
            "lifecycle_status", "registration_status", "updated_at",
        ])
        OutboxEvent.objects.filter(
            idempotency_key=f"pid-register:{identifier.pk}",
            status__in=[OutboxEvent.Status.PENDING, OutboxEvent.Status.FAILED],
        ).update(
            status=OutboxEvent.Status.CANCELLED,
            failed_at=timezone.now(),
            last_error="Release was withdrawn before identifier registration.",
            locked_at=None,
            locked_until=None,
            locked_by=None,
        )


def _safe_error(message):
    return sanitize_pid_text(message)


def _safe_response_summary(result, assigned_value):
    summary = {
        "identifier_value": assigned_value,
        "landing_page_url": (result.landing_page_url or "")[:2048],
    }
    provider_reference = result.response_summary.get("provider_reference")
    if isinstance(provider_reference, (str, int)):
        summary["provider_reference"] = str(provider_reference)[:255]
    return summary


def _retry_delay(attempt_number):
    return timedelta(seconds=min(30 * (2 ** max(0, attempt_number - 1)), MAX_BACKOFF_SECONDS))


def claim_next_outbox_event(*, worker_id=None, lease_seconds=DEFAULT_LEASE_SECONDS):
    """Claim one event. PostgreSQL workers use SKIP LOCKED to avoid duplicate claims."""
    worker_id = worker_id or uuid.uuid4()
    now = timezone.now()
    with transaction.atomic():
        event = (
            OutboxEvent.objects.select_for_update(skip_locked=True)
            .filter(
                models_pending_or_expired(now),
            )
            .order_by("available_at", "created_at")
            .first()
        )
        if event is None:
            return None

        if event.status == OutboxEvent.Status.PROCESSING:
            last_attempt = event.payload.get("_active_attempt_id")
            if last_attempt:
                DOIRegistrationAttempt.objects.filter(
                    pk=last_attempt,
                    status=DOIRegistrationAttempt.Status.PENDING,
                ).update(
                    status=DOIRegistrationAttempt.Status.FAILED,
                    error_information="Processing lease expired before completion.",
                )
            event.status = OutboxEvent.Status.PENDING
            event.locked_at = event.locked_until = event.locked_by = None
            event.last_error = "Previous worker lease expired; event reclaimed."
            event.save(update_fields=[
                "status", "locked_at", "locked_until", "locked_by", "last_error", "updated_at",
            ])

        event.status = OutboxEvent.Status.PROCESSING
        event.locked_at = now
        event.locked_until = now + timedelta(seconds=lease_seconds)
        event.locked_by = worker_id
        event.save(update_fields=[
            "status", "locked_at", "locked_until", "locked_by", "updated_at",
        ])
        return event.pk, worker_id


def models_pending_or_expired(now):
    from django.db.models import Q

    return (
        Q(status=OutboxEvent.Status.PENDING, available_at__lte=now)
        | Q(status=OutboxEvent.Status.PROCESSING, locked_until__lte=now)
    )


def _start_registration_attempt(event_id, worker_id):
    event_data = OutboxEvent.objects.filter(pk=event_id).values(
        "event_type", "payload", "idempotency_key"
    ).first()
    if not event_data or event_data["event_type"] != EVENT_REGISTER_IDENTIFIER:
        return None, None, "Unsupported or missing outbox event."

    try:
        identifier_id = uuid.UUID(event_data["payload"]["identifier_id"])
    except (KeyError, TypeError, ValueError):
        return None, None, "Outbox event has an invalid identifier reference."

    target = ResourceIdentifier.objects.filter(pk=identifier_id).values(
        "dataset_id", "release_id", "scheme"
    ).first()
    if target is None:
        return None, None, "Outbox event references a missing identifier."
    if target["scheme"] != ResourceIdentifier.Scheme.DOI:
        return None, None, "Only DOI-scheme identifiers may be submitted to a DOI provider."
    dataset_id = target["dataset_id"]
    release_id = target["release_id"]
    if release_id:
        dataset_id = DatasetRelease.objects.values_list(
            "dataset_id", flat=True
        ).get(pk=release_id)

    with transaction.atomic():
        Dataset.objects.select_for_update().get(pk=dataset_id)
        release = None
        if release_id:
            release = DatasetRelease.objects.select_for_update().get(pk=release_id)
        event = OutboxEvent.objects.select_for_update().get(pk=event_id)
        if (
            event.status != OutboxEvent.Status.PROCESSING
            or event.locked_by != worker_id
        ):
            return None, None, "The event lease is no longer owned by this worker."
        identifier = ResourceIdentifier.objects.select_for_update().get(pk=identifier_id)
        if (
            identifier.lifecycle_status != ResourceIdentifier.LifecycleStatus.ACTIVE
            or (release is not None and release.status != DatasetRelease.Status.PUBLISHED)
        ):
            identifier.lifecycle_status = ResourceIdentifier.LifecycleStatus.WITHDRAWN
            if identifier.registration_status != ResourceIdentifier.RegistrationStatus.REGISTERED:
                identifier.registration_status = ResourceIdentifier.RegistrationStatus.CANCELLED
            identifier.save(update_fields=[
                "lifecycle_status", "registration_status", "updated_at",
            ])
            event.status = OutboxEvent.Status.CANCELLED
            event.failed_at = timezone.now()
            event.last_error = "The release is not eligible for identifier registration."
            event.locked_at = event.locked_until = event.locked_by = None
            event.save(update_fields=[
                "status", "failed_at", "last_error", "payload", "locked_at", "locked_until",
                "locked_by", "updated_at",
            ])
            return None, None, "cancelled"

        if identifier.registration_status == ResourceIdentifier.RegistrationStatus.REGISTERED:
            event.status = OutboxEvent.Status.COMPLETED
            event.completed_at = timezone.now()
            event.locked_at = event.locked_until = event.locked_by = None
            event.save(update_fields=[
                "status", "completed_at", "locked_at", "locked_until", "locked_by",
                "updated_at",
            ])
            return None, None, "already_registered"

        manual_retry_authorized = bool(event.payload.get("_manual_retry_authorized"))
        if event.retry_count >= MAX_REGISTRATION_ATTEMPTS and not manual_retry_authorized:
            event.status = OutboxEvent.Status.FAILED
            event.failed_at = timezone.now()
            event.last_error = "Maximum DOI registration attempts reached."
            event.payload = {**event.payload, "_retry_exhausted": True}
            event.locked_at = event.locked_until = event.locked_by = None
            identifier.registration_status = ResourceIdentifier.RegistrationStatus.FAILED
            identifier.save(update_fields=["registration_status", "updated_at"])
            event.save(update_fields=[
                "status", "failed_at", "last_error", "locked_at", "locked_until",
                "locked_by", "updated_at",
            ])
            return None, None, "failed"

        event.retry_count += 1
        event.payload = {
            key: value for key, value in event.payload.items()
            if key != "_manual_retry_authorized"
        }
        attempt_number = max(
            event.retry_count,
            DOIRegistrationAttempt.objects.filter(identifier=identifier).values_list(
                "attempt_number", flat=True
            ).order_by("-attempt_number").first() or 0,
        )
        attempt = DOIRegistrationAttempt.objects.create(
            identifier=identifier,
            attempt_number=attempt_number,
            request_summary={
                "scheme": identifier.scheme,
                "role": identifier.role,
                "identifier_value": identifier.identifier_value,
                "landing_page_url": identifier.landing_page_url,
            },
        )
        event.payload = {
            **event.payload,
            "_active_attempt_id": str(attempt.pk),
        }
        event.save(update_fields=["retry_count", "payload", "updated_at"])
        identifier.registration_status = ResourceIdentifier.RegistrationStatus.PENDING
        identifier.save(update_fields=["registration_status", "updated_at"])
        return identifier, attempt, event.idempotency_key


def _finish_registration(event_id, worker_id, identifier, attempt, result=None, error=None):
    now = timezone.now()
    with transaction.atomic():
        event = OutboxEvent.objects.select_for_update().get(pk=event_id)
        identifier = ResourceIdentifier.objects.select_for_update().get(pk=identifier.pk)
        attempt = DOIRegistrationAttempt.objects.select_for_update().get(pk=attempt.pk)
        if event.status != OutboxEvent.Status.PROCESSING or event.locked_by != worker_id:
            return "lease_lost"

        if error is None:
            value = (result.identifier_value or "").strip()
            if not value:
                error = PermanentDOIProviderError("Provider returned no identifier value.")
            elif identifier.identifier_value not in (None, value):
                error = PermanentDOIProviderError(
                    "Provider returned a value that conflicts with the assigned identifier."
                )
            else:
                identifier.identifier_value = value
                identifier.landing_page_url = result.landing_page_url or ""
                identifier.registration_status = ResourceIdentifier.RegistrationStatus.REGISTERED
                identifier.registered_at = identifier.registered_at or now
                attempt.status = DOIRegistrationAttempt.Status.SUCCEEDED
                attempt.response_summary = _safe_response_summary(result, value)
                try:
                    with transaction.atomic():
                        attempt.save(update_fields=["status", "response_summary"])
                        identifier.save(update_fields=[
                            "identifier_value", "landing_page_url", "registration_status",
                            "registered_at", "updated_at",
                        ])
                except (ValidationError, IntegrityError):
                    error = PermanentDOIProviderError(
                        "Provider returned invalid or duplicate identifier data."
                    )
                else:
                    event.status = OutboxEvent.Status.COMPLETED
                    event.completed_at = now
                    event.last_error = ""
                    event.payload = {
                        key: value for key, value in event.payload.items()
                        if key != "_active_attempt_id"
                    }
                    event.locked_at = event.locked_until = event.locked_by = None
                    event.save(update_fields=[
                        "status", "completed_at", "last_error", "payload", "locked_at",
                        "locked_until", "locked_by", "updated_at",
                    ])
                    return "completed"

        error_text = _safe_error(error)
        attempt.status = DOIRegistrationAttempt.Status.FAILED
        attempt.error_information = error_text
        attempt.save(update_fields=["status", "error_information"])
        if identifier.lifecycle_status == ResourceIdentifier.LifecycleStatus.WITHDRAWN:
            identifier.registration_status = ResourceIdentifier.RegistrationStatus.CANCELLED
            event.status = OutboxEvent.Status.CANCELLED
            event.failed_at = now
        elif isinstance(error, PermanentDOIProviderError):
            identifier.registration_status = ResourceIdentifier.RegistrationStatus.FAILED
            event.status = OutboxEvent.Status.FAILED
            event.failed_at = now
            event.payload = {**event.payload, "_permanent_failure": True}
        elif event.retry_count < MAX_REGISTRATION_ATTEMPTS:
            identifier.registration_status = ResourceIdentifier.RegistrationStatus.PENDING
            event.status = OutboxEvent.Status.PENDING
            event.available_at = now + _retry_delay(event.retry_count)
        else:
            identifier.registration_status = ResourceIdentifier.RegistrationStatus.FAILED
            event.status = OutboxEvent.Status.FAILED
            event.failed_at = now
            event.payload = {**event.payload, "_retry_exhausted": True}
        identifier.save(update_fields=["registration_status", "updated_at"])
        event.last_error = error_text
        event.payload = {
            key: value for key, value in event.payload.items()
            if key != "_active_attempt_id"
        }
        event.locked_at = event.locked_until = event.locked_by = None
        event.save(update_fields=[
            "status", "available_at", "failed_at", "last_error", "payload",
            "locked_at", "locked_until", "locked_by", "updated_at",
        ])
        return event.status


def _mark_event_failed(event_id, worker_id, error):
    with transaction.atomic():
        event = OutboxEvent.objects.select_for_update().filter(pk=event_id).first()
        if (
            event is None
            or event.status != OutboxEvent.Status.PROCESSING
            or event.locked_by != worker_id
        ):
            return "lease_lost"
        event.status = OutboxEvent.Status.FAILED
        event.failed_at = timezone.now()
        event.last_error = _safe_error(error)
        event.payload = {**event.payload, "_permanent_failure": True}
        event.locked_at = event.locked_until = event.locked_by = None
        event.save(update_fields=[
            "status", "failed_at", "last_error", "payload", "locked_at", "locked_until",
            "locked_by", "updated_at",
        ])
        return "failed"


def retry_failed_outbox_event(event_id):
    """Requeue one eligible failed event after explicit operator action.

    Permanent failures are intentionally not retryable until their cause is
    corrected and an operator explicitly clears the permanent marker.
    """
    with transaction.atomic():
        event = OutboxEvent.objects.select_for_update().get(pk=event_id)
        if event.status != OutboxEvent.Status.FAILED:
            raise ValidationError("Only failed outbox events can be retried.")
        if event.payload.get("_permanent_failure"):
            raise ValidationError(
                "This event is marked as a permanent failure; correct the provider/request "
                "problem and use the explicit permanent-failure override."
            )
        if event.retry_count >= MAX_OUTBOX_RETRIES:
            raise ValidationError("The event has reached the configured retry limit.")
        identifier_id = event.payload.get("identifier_id")
        identifier = ResourceIdentifier.objects.select_for_update().filter(
            pk=identifier_id
        ).first()
        if identifier is None:
            raise ValidationError("The event references a missing identifier.")
        if identifier.scheme != ResourceIdentifier.Scheme.DOI:
            raise ValidationError("Only DOI-scheme identifiers can be retried with a DOI provider.")
        if identifier.lifecycle_status != ResourceIdentifier.LifecycleStatus.ACTIVE:
            raise ValidationError("Withdrawn identifiers cannot be retried.")
        if identifier.registration_status == ResourceIdentifier.RegistrationStatus.REGISTERED:
            raise ValidationError("Already registered identifiers cannot be retried.")
        event.status = OutboxEvent.Status.PENDING
        event.available_at = timezone.now()
        event.failed_at = None
        event.last_error = ""
        event.save(update_fields=["status", "available_at", "failed_at", "last_error", "updated_at"])
        identifier.registration_status = ResourceIdentifier.RegistrationStatus.PENDING
        identifier.save(update_fields=["registration_status", "updated_at"])
        return event


def clear_permanent_failure_and_retry(event_id, *, reason):
    """Explicitly override a permanent failure after operator remediation."""
    reason = sanitize_pid_text(reason).strip()
    if not reason:
        raise ValidationError("An operator reason is required.")
    with transaction.atomic():
        event = OutboxEvent.objects.select_for_update().get(pk=event_id)
        if event.status != OutboxEvent.Status.FAILED or not (
            event.payload.get("_permanent_failure") or event.payload.get("_retry_exhausted")
        ):
            raise ValidationError("The event is not an override-eligible terminal failure.")
        event.payload = {
            key: value for key, value in event.payload.items()
            if key not in {"_permanent_failure", "_retry_exhausted"}
        }
        event.payload["_manual_retry_authorized"] = True
        event.last_error = f"Manual retry authorized: {reason}"
        event.status = OutboxEvent.Status.PENDING
        event.available_at = timezone.now()
        event.failed_at = None
        event.save(update_fields=["payload", "last_error", "status", "available_at", "failed_at", "updated_at"])
        identifier = ResourceIdentifier.objects.select_for_update().get(
            pk=event.payload["identifier_id"]
        )
        if identifier.scheme != ResourceIdentifier.Scheme.DOI:
            raise ValidationError("Only DOI-scheme identifiers can be retried with a DOI provider.")
        if identifier.lifecycle_status != ResourceIdentifier.LifecycleStatus.ACTIVE:
            raise ValidationError("Withdrawn identifiers cannot be retried.")
        identifier.registration_status = ResourceIdentifier.RegistrationStatus.PENDING
        identifier.save(update_fields=["registration_status", "updated_at"])
        return event


def process_claimed_event(event_id, worker_id, provider=None):
    # High‑level entry point for processing a single outbox event.
    # If DOI registration is globally disabled, exit early.
    if not getattr(settings, "ORDP_DOI_REGISTRATION_ENABLED", False):
        return "disabled"
    # Retrieve the identifier and attempt details.
    identifier, attempt, idempotency_key = _start_registration_attempt(
        event_id, worker_id
    )
    # If the identifier could not be resolved (e.g., missing), handle according to prior logic.
    if identifier is None:
        if idempotency_key not in {"cancelled", "already_registered", "failed"}:
            return _mark_event_failed(event_id, worker_id, idempotency_key)
        return idempotency_key
    # Skip registration for identifiers without a configured provider.
    if identifier.provider in ("", "unconfigured"):
        # Mark the event as cancelled without error.
        from apps.datasets.models import OutboxEvent
        with transaction.atomic():
            event = OutboxEvent.objects.select_for_update().get(pk=event_id)
            identifier = ResourceIdentifier.objects.select_for_update().get(pk=identifier.pk)
            if identifier.registration_status != ResourceIdentifier.RegistrationStatus.REGISTERED:
                identifier.registration_status = ResourceIdentifier.RegistrationStatus.CANCELLED
                identifier.save(update_fields=["registration_status", "updated_at"])
            event.status = OutboxEvent.Status.CANCELLED
            event.failed_at = timezone.now()
            event.last_error = "Identifier provider not configured."
            event.locked_at = event.locked_until = event.locked_by = None
            event.save(update_fields=[
                "status", "failed_at", "last_error",
                "locked_at", "locked_until", "locked_by", "updated_at",
            ])
        return "cancelled"
    # Proceed with registration using the appropriate provider.
    try:
        provider = provider or get_doi_provider(identifier.provider)
        result = provider.register(identifier, idempotency_key=idempotency_key)
        if not isinstance(result, RegistrationResult):
            raise PermanentDOIProviderError("Provider returned an invalid registration result.")
        if (
            not isinstance(result.identifier_value, str)
            or not result.identifier_value.strip()
            or not isinstance(result.landing_page_url, str)
            or not isinstance(result.response_summary, dict)
        ):
            raise PermanentDOIProviderError("Provider returned invalid registration data.")
    except Exception as exc:
        return _finish_registration(
            event_id, worker_id, identifier, attempt, error=exc
        )
    return _finish_registration(
        event_id, worker_id, identifier, attempt, result=result
    )
