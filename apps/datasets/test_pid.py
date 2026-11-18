import uuid
from datetime import timedelta
from io import StringIO
from importlib import import_module
from threading import Thread
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import DatabaseError, close_old_connections, connection, transaction
from django.apps import apps as django_apps
from django.test import TestCase, TransactionTestCase, override_settings, skipUnlessDBFeature
from django.utils import timezone
from rest_framework.test import APIClient

from apps.datasets.factories import make_user
from apps.datasets.models import (
    DOIRegistrationAttempt,
    Dataset,
    DatasetFile,
    DatasetProposal,
    DatasetProposalVote,
    DatasetRelease,
    OutboxEvent,
    PIDRegistrationEvent,
    ResourceIdentifier,
    ResourceRelationship,
    StoredObject,
)
from apps.datasets.services.pid import (
    MAX_REGISTRATION_ATTEMPTS,
    MockDOIProvider,
    RegistrationResult,
    claim_next_outbox_event,
    ensure_release_identifiers,
    process_claimed_event,
    PermanentDOIProviderError,
    retry_failed_outbox_event,
    clear_permanent_failure_and_retry,
)
from apps.datasets.services.proposals import submit_proposal, submit_proposal_vote
from apps.datasets.services.publication import (
    create_dataset_proposal,
    publish_initial_dataset,
    publish_proposal,
    withdraw_release,
)


def _stored_object(dataset, name="pid.csv"):
    object_id = uuid.uuid4()
    return StoredObject.objects.create(
        id=object_id,
        dataset=dataset,
        storage_key=StoredObject.build_storage_key(dataset.pk, object_id, name),
        original_filename=name,
        file_type="csv",
        file_size=8,
        checksum_sha256="a" * 64,
        verified_at=timezone.now(),
    )


def _published_dataset(owner):
    dataset = Dataset.objects.create(
        title="PID dataset",
        owner=owner,
        status=Dataset.Status.PUBLISHED,
    )
    stored = _stored_object(dataset)
    DatasetFile.objects.create(
        dataset=dataset,
        file_key=stored.storage_key,
        original_filename=stored.original_filename,
        file_type=stored.file_type,
        file_size=stored.file_size,
        checksum=stored.checksum_sha256,
        stored_object=stored,
    )
    return dataset


def _doi_payload(response):
    """Return the DOI item when an API response also contains ORDP PIDs."""
    return next(item for item in response.data if item.get("scheme") == "doi")


def _mark_registered(identifier, value="10.5555/provider-assigned-test-value"):
    attempt = DOIRegistrationAttempt.objects.create(
        identifier=identifier,
        attempt_number=1,
        request_summary={"scheme": identifier.scheme},
    )
    attempt.status = DOIRegistrationAttempt.Status.SUCCEEDED
    attempt.response_summary = {"identifier_value": value}
    attempt.save(update_fields=["status", "response_summary"])
    identifier.identifier_value = value
    identifier.registration_status = ResourceIdentifier.RegistrationStatus.REGISTERED
    identifier.registered_at = timezone.now()
    identifier.save(update_fields=[
        "identifier_value", "registration_status", "registered_at", "updated_at",
    ])


class ResourceIdentifierModelTests(TestCase):
    def setUp(self):
        self.owner = make_user("pidmodelowner", "pidmodelowner@aastu.edu.et")
        self.dataset = Dataset.objects.create(title="PID model", owner=self.owner)
        self.release = DatasetRelease.objects.create(
            dataset=self.dataset,
            major=1,
            minor=0,
            patch=0,
            status=DatasetRelease.Status.DRAFT,
            metadata_snapshot={"schema_version": 1},
        )

    def test_valid_concept_and_version_identifiers(self):
        concept = ResourceIdentifier.objects.create(
            dataset=self.dataset,
            role=ResourceIdentifier.Role.CONCEPT,
        )
        version = ResourceIdentifier.objects.create(
            release=self.release,
            role=ResourceIdentifier.Role.VERSION,
        )
        self.assertEqual(concept.dataset_id, self.dataset.pk)
        self.assertEqual(version.release_id, self.release.pk)

    def test_invalid_role_target_combinations_are_rejected(self):
        with self.assertRaises(ValidationError):
            ResourceIdentifier.objects.create(
                release=self.release,
                role=ResourceIdentifier.Role.CONCEPT,
            )
        with self.assertRaises(ValidationError):
            ResourceIdentifier.objects.create(
                dataset=self.dataset,
                role=ResourceIdentifier.Role.VERSION,
            )

    def test_duplicate_concept_identifier_per_dataset_and_scheme_is_rejected(self):
        ResourceIdentifier.objects.create(
            dataset=self.dataset,
            role=ResourceIdentifier.Role.CONCEPT,
        )
        with self.assertRaises(ValidationError):
            ResourceIdentifier.objects.create(
                dataset=self.dataset,
                role=ResourceIdentifier.Role.CONCEPT,
            )

    def test_duplicate_identifier_values_are_rejected(self):
        other_dataset = Dataset.objects.create(title="Other PID", owner=self.owner)
        first = ResourceIdentifier.objects.create(
            dataset=self.dataset,
            role=ResourceIdentifier.Role.CONCEPT,
        )
        _mark_registered(first)
        duplicate = ResourceIdentifier.objects.create(
            dataset=other_dataset,
            role=ResourceIdentifier.Role.CONCEPT,
        )
        with self.assertRaises(ValidationError):
            _mark_registered(duplicate, "10.5555/PROVIDER-ASSIGNED-TEST-VALUE")

    def test_relationship_validation_and_self_reference_protection(self):
        concept = ResourceIdentifier.objects.create(
            dataset=self.dataset,
            role=ResourceIdentifier.Role.CONCEPT,
        )
        version = ResourceIdentifier.objects.create(
            release=self.release,
            role=ResourceIdentifier.Role.VERSION,
        )
        relation = ResourceRelationship.objects.create(
            subject_identifier=concept,
            related_identifier=version,
            relationship_type=ResourceRelationship.Type.HAS_VERSION,
        )
        self.assertEqual(relation.relationship_type, "hasVersion")
        with self.assertRaises(ValidationError):
            ResourceRelationship.objects.create(
                subject_identifier=concept,
                related_identifier=concept,
                relationship_type=ResourceRelationship.Type.HAS_VERSION,
            )
        with self.assertRaises(ValidationError):
            ResourceRelationship.objects.create(
                subject_identifier=version,
                related_identifier=concept,
                relationship_type=ResourceRelationship.Type.HAS_VERSION,
            )

    def test_official_link_is_only_exposed_after_registration(self):
        pending = ResourceIdentifier.objects.create(
            dataset=self.dataset,
            role=ResourceIdentifier.Role.CONCEPT,
        )
        self.assertIsNone(pending.official_doi_link)
        _mark_registered(pending)
        self.assertEqual(
            pending.official_doi_link,
            "https://doi.org/10.5555/provider-assigned-test-value",
        )

    def test_application_cannot_mark_an_identifier_registered_without_successful_attempt(self):
        identifier = ResourceIdentifier.objects.create(
            dataset=self.dataset,
            role=ResourceIdentifier.Role.CONCEPT,
        )
        identifier.identifier_value = "10.5555/provider-assigned-test-value"
        identifier.registration_status = ResourceIdentifier.RegistrationStatus.REGISTERED
        identifier.registered_at = timezone.now()
        with self.assertRaises(ValidationError):
            identifier.save(update_fields=[
                "identifier_value", "registration_status", "registered_at", "updated_at",
            ])


class PIDPublicationTests(TestCase):
    def setUp(self):
        self.verify_patcher = patch(
            "apps.datasets.services.publication.verify_stored_object_in_storage"
        )
        self.verify_patcher.start()
        self.addCleanup(self.verify_patcher.stop)
        self.owner = make_user("pidpubowner", "pidpubowner@aastu.edu.et")
        self.dataset = _published_dataset(self.owner)

    def test_initial_release_creates_concept_version_relationships_and_events(self):
        release = publish_initial_dataset(self.dataset, self.owner)
        concept = ResourceIdentifier.objects.get(
            dataset=self.dataset,
            role=ResourceIdentifier.Role.CONCEPT,
            scheme="doi",
        )
        version = ResourceIdentifier.objects.get(
            release=release,
            role=ResourceIdentifier.Role.VERSION,
            scheme="doi",
        )
        self.assertIsNone(concept.identifier_value)
        self.assertEqual(
            concept.registration_status,
            ResourceIdentifier.RegistrationStatus.PENDING,
        )
        self.assertEqual(
            ResourceRelationship.objects.filter(
                subject_identifier=concept,
                related_identifier=version,
            ).count(),
            1,
        )
        self.assertEqual(OutboxEvent.objects.count(), 2)

    def test_retry_does_not_duplicate_identifiers_or_events(self):
        first = publish_initial_dataset(self.dataset, self.owner)
        publish_initial_dataset(self.dataset, self.owner)
        ensure_release_identifiers(first)
        self.assertEqual(ResourceIdentifier.objects.filter(scheme="doi").count(), 2)
        self.assertEqual(OutboxEvent.objects.count(), 2)
        self.assertEqual(
            ResourceRelationship.objects.filter(
                subject_identifier__scheme="doi",
                related_identifier__scheme="doi",
            ).count(),
            2,
        )

    def test_draft_proposal_does_not_receive_a_public_identifier(self):
        release = publish_initial_dataset(self.dataset, self.owner)
        create_dataset_proposal(
            dataset=self.dataset,
            base_release=release,
            submitted_by=self.owner,
        )
        self.assertEqual(ResourceIdentifier.objects.filter(scheme="doi").count(), 2)
        self.assertEqual(OutboxEvent.objects.count(), 2)

    def test_subsequent_release_reuses_concept_and_gets_its_own_version_identifier(self):
        base = publish_initial_dataset(self.dataset, self.owner)

        proposal = create_dataset_proposal(
            dataset=self.dataset,
            base_release=base,
            submitted_by=self.owner,
            declared_change_type="patch",
        )

        # Make a real metadata change so this is not a no-op release.
        proposal.proposed_metadata = {
            "description": (
                base.metadata_snapshot.get("description", "")
                + " PID publication test update."
            )
        }
        proposal.save(update_fields=["proposed_metadata"])

        self.assertEqual(
            proposal.base_release.status,
            DatasetRelease.Status.PUBLISHED,
        )

        proposal = submit_proposal(proposal, self.owner, "patch")

        self.assertEqual(
            proposal.status,
            DatasetProposal.Status.PENDING_REVIEW,
        )

        reviewers = [
            make_user(
                f"pidreviewer{index}",
                f"pidreviewer{index}@aastu.edu.et",
                role="reviewer",
            )
            for index in range(3)
        ]

        for reviewer in reviewers:
            submit_proposal_vote(
                proposal,
                reviewer,
                DatasetProposalVote.Vote.APPROVE,
            )

        proposal.refresh_from_db()

        self.assertEqual(
            proposal.status,
            DatasetProposal.Status.APPROVED,
        )

        release = publish_proposal(proposal, self.owner, "patch")

        self.assertEqual(release.version, "1.0.1")

        self.assertEqual(
            ResourceIdentifier.objects.filter(
                dataset=self.dataset,
                role=ResourceIdentifier.Role.CONCEPT,
                scheme="doi",
            ).count(),
            1,
        )

        concept = ResourceIdentifier.objects.get(
            dataset=self.dataset,
            role=ResourceIdentifier.Role.CONCEPT,
            scheme="doi",
        )

        version = ResourceIdentifier.objects.get(
            release=release,
            role=ResourceIdentifier.Role.VERSION,
            scheme="doi",
        )

        self.assertEqual(
            ResourceRelationship.objects.filter(
                subject_identifier=concept,
                related_identifier=version,
            ).count(),
            1,
        )

        self.assertEqual(OutboxEvent.objects.count(), 3)
    @override_settings(ORDP_DOI_REGISTRATION_POLICY="dataset_only")
    def test_dataset_only_policy_creates_only_concept_identifier(self):
        release = publish_initial_dataset(self.dataset, self.owner)
        self.assertTrue(ResourceIdentifier.objects.filter(
            dataset=self.dataset, role=ResourceIdentifier.Role.CONCEPT, scheme="doi"
        ).exists())
        self.assertFalse(ResourceIdentifier.objects.filter(
            release=release, role=ResourceIdentifier.Role.VERSION, scheme="doi"
        ).exists())
        self.assertEqual(OutboxEvent.objects.count(), 1)

    @override_settings(ORDP_DOI_REGISTRATION_POLICY="release_only")
    def test_release_only_policy_creates_only_version_identifier(self):
        release = publish_initial_dataset(self.dataset, self.owner)
        self.assertFalse(ResourceIdentifier.objects.filter(
            dataset=self.dataset, role=ResourceIdentifier.Role.CONCEPT, scheme="doi"
        ).exists())
        self.assertTrue(ResourceIdentifier.objects.filter(
            release=release, role=ResourceIdentifier.Role.VERSION, scheme="doi"
        ).exists())
        self.assertEqual(OutboxEvent.objects.count(), 1)

    @override_settings(ORDP_DOI_REGISTRATION_POLICY="disabled")
    def test_disabled_policy_creates_no_doi_records_or_events(self):
        publish_initial_dataset(self.dataset, self.owner)
        self.assertEqual(ResourceIdentifier.objects.filter(scheme="doi").count(), 0)
        self.assertEqual(OutboxEvent.objects.count(), 0)

    def test_datacite_previous_and_next_version_relationships_use_release_numbers(self):
        older_release = publish_initial_dataset(self.dataset, self.owner)
        older = ResourceIdentifier.objects.get(release=older_release, role=ResourceIdentifier.Role.VERSION, scheme="doi")
        newer_release = DatasetRelease.objects.create(
            dataset=self.dataset, major=1, minor=0, patch=1,
            status=DatasetRelease.Status.DRAFT,
            metadata_snapshot={"schema_version": 1},
        )
        newer = ResourceIdentifier.objects.create(release=newer_release, role=ResourceIdentifier.Role.VERSION)
        forward = ResourceRelationship.objects.create(
            subject_identifier=newer, related_identifier=older,
            relationship_type=ResourceRelationship.Type.IS_NEW_VERSION_OF,
        )
        reverse = ResourceRelationship.objects.create(
            subject_identifier=older, related_identifier=newer,
            relationship_type=ResourceRelationship.Type.IS_PREVIOUS_VERSION_OF,
        )
        self.assertEqual(forward.relationship_type, "isNewVersionOf")
        self.assertEqual(reverse.relationship_type, "isPreviousVersionOf")
        with self.assertRaises(ValidationError):
            ResourceRelationship.objects.create(
                subject_identifier=older, related_identifier=newer,
                relationship_type=ResourceRelationship.Type.IS_NEW_VERSION_OF,
            )

    def test_related_dataset_identifier_relationship_is_supported(self):
        other_dataset = Dataset.objects.create(title="Related dataset", owner=self.owner)
        subject = ResourceIdentifier.objects.create(dataset=self.dataset, role=ResourceIdentifier.Role.CONCEPT)
        related = ResourceIdentifier.objects.create(dataset=other_dataset, role=ResourceIdentifier.Role.CONCEPT)
        relationship = ResourceRelationship.objects.create(
            subject_identifier=subject, related_identifier=related,
            relationship_type=ResourceRelationship.Type.IS_DERIVED_FROM,
        )
        self.assertEqual(relationship.relationship_type, "isDerivedFrom")


class PIDPolicyReconciliationTests(TestCase):
    @patch("apps.datasets.services.publication.verify_stored_object_in_storage")
    @override_settings(ORDP_DOI_REGISTRATION_POLICY="dataset_and_release")
    def test_dataset_only_reconciliation_preserves_registered_concept_and_cancels_version_work(self, _verify):
        owner = make_user("pid-policy-owner", "pid-policy-owner@aastu.edu.et")
        dataset = _published_dataset(owner)
        release = publish_initial_dataset(dataset, owner)
        concept = ResourceIdentifier.objects.get(dataset=dataset, role=ResourceIdentifier.Role.CONCEPT, scheme="doi")
        version = ResourceIdentifier.objects.get(release=release, role=ResourceIdentifier.Role.VERSION, scheme="doi")
        _mark_registered(concept)

        with override_settings(ORDP_DOI_REGISTRATION_POLICY="dataset_only"):
            call_command("reconcile_pid_policy")

        concept.refresh_from_db()
        version.refresh_from_db()
        version_event = OutboxEvent.objects.get(idempotency_key=f"pid-register:{version.pk}")
        self.assertEqual(concept.registration_status, ResourceIdentifier.RegistrationStatus.REGISTERED)
        self.assertEqual(version.registration_status, ResourceIdentifier.RegistrationStatus.NOT_REQUESTED)
        self.assertEqual(version_event.status, OutboxEvent.Status.CANCELLED)

    @patch("apps.datasets.services.publication.verify_stored_object_in_storage")
    @override_settings(ORDP_DOI_REGISTRATION_POLICY="dataset_and_release")
    def test_policy_reconciliation_dry_run_does_not_mutate_identifiers(self, _verify):
        owner = make_user("pid-policy-dry-owner", "pid-policy-dry-owner@aastu.edu.et")
        dataset = _published_dataset(owner)
        release = publish_initial_dataset(dataset, owner)
        version = ResourceIdentifier.objects.get(release=release, role=ResourceIdentifier.Role.VERSION, scheme="doi")
        with override_settings(ORDP_DOI_REGISTRATION_POLICY="dataset_only"):
            call_command("reconcile_pid_policy", dry_run=True)
        version.refresh_from_db()
        self.assertEqual(version.registration_status, ResourceIdentifier.RegistrationStatus.PENDING)


class PIDOutboxTests(TestCase):
    def setUp(self):
        self.owner = make_user("pidoutboxowner", "pidoutboxowner@aastu.edu.et")
        self.dataset = Dataset.objects.create(title="Outbox dataset", owner=self.owner)
        self.identifier = ResourceIdentifier.objects.create(
            dataset=self.dataset,
            role=ResourceIdentifier.Role.CONCEPT,
            provider="mock",
            registration_status=ResourceIdentifier.RegistrationStatus.PENDING,
        )
        self.event = OutboxEvent.objects.create(
            event_type="pid.register_identifier",
            payload={"identifier_id": str(self.identifier.pk)},
            idempotency_key=f"test-pid:{self.identifier.pk}",
        )

    @override_settings(ORDP_DOI_REGISTRATION_ENABLED=True)
    def test_successful_mock_registration_records_result_and_completes_event(self):
        claim = claim_next_outbox_event()
        result = RegistrationResult(
            identifier_value="10.5555/provider-assigned-test-value",
            landing_page_url="https://example.test/datasets/one",
            response_summary={"provider_reference": "provider-job-1"},
        )
        outcome = process_claimed_event(
            *claim,
            provider=MockDOIProvider(responses={self.identifier.pk: result}),
        )
        self.assertEqual(outcome, "completed")
        self.identifier.refresh_from_db()
        self.event.refresh_from_db()
        self.assertEqual(self.event.status, OutboxEvent.Status.COMPLETED)
        self.assertEqual(
            self.identifier.registration_status,
            ResourceIdentifier.RegistrationStatus.REGISTERED,
        )
        self.assertEqual(self.identifier.identifier_value, result.identifier_value)
        self.assertEqual(self.identifier.registration_attempts.count(), 1)
        from apps.datasets.models import PIDRegistrationEvent
        self.assertEqual(
            list(PIDRegistrationEvent.objects.filter(event_id=self.event.pk).values_list("to_status", flat=True)),
            [OutboxEvent.Status.PENDING, OutboxEvent.Status.PROCESSING, OutboxEvent.Status.COMPLETED],
        )

    @override_settings(ORDP_DOI_REGISTRATION_ENABLED=True)
    def test_failure_is_recorded_and_rescheduled_with_backoff(self):
        claim = claim_next_outbox_event()
        outcome = process_claimed_event(
            *claim,
            provider=MockDOIProvider(errors={
                self.identifier.pk: RuntimeError("provider temporarily unavailable"),
            }),
        )
        self.assertEqual(outcome, OutboxEvent.Status.PENDING)
        self.event.refresh_from_db()
        self.identifier.refresh_from_db()
        self.assertGreater(self.event.available_at, timezone.now())
        self.assertEqual(
            self.identifier.registration_attempts.get().status,
            DOIRegistrationAttempt.Status.FAILED,
        )
        self.assertEqual(
            self.identifier.registration_status,
            ResourceIdentifier.RegistrationStatus.PENDING,
        )

    @override_settings(ORDP_DOI_REGISTRATION_ENABLED=True)
    def test_retries_are_bounded(self):
        for _ in range(MAX_REGISTRATION_ATTEMPTS):
            self.event.refresh_from_db()
            OutboxEvent.objects.filter(pk=self.event.pk).update(
                available_at=timezone.now(),
                status=OutboxEvent.Status.PENDING,
                locked_at=None,
                locked_until=None,
                locked_by=None,
            )
            claim = claim_next_outbox_event()
            process_claimed_event(
                *claim,
                provider=MockDOIProvider(errors={
                    self.identifier.pk: RuntimeError("provider failure"),
                }),
            )
        self.event.refresh_from_db()
        self.identifier.refresh_from_db()
        self.assertEqual(self.event.status, OutboxEvent.Status.FAILED)
        self.assertEqual(self.event.retry_count, MAX_REGISTRATION_ATTEMPTS)
        self.assertEqual(
            self.identifier.registration_status,
            ResourceIdentifier.RegistrationStatus.FAILED,
        )

    def test_expired_lease_is_reclaimed_and_pending_attempt_is_closed(self):
        attempt = DOIRegistrationAttempt.objects.create(
            identifier=self.identifier,
            attempt_number=1,
            request_summary={"role": "concept"},
        )
        self.event.status = OutboxEvent.Status.PROCESSING
        self.event.retry_count = 1
        self.event.locked_at = timezone.now() - timedelta(minutes=10)
        self.event.locked_until = timezone.now() - timedelta(minutes=5)
        self.event.locked_by = uuid.uuid4()
        self.event.payload["_active_attempt_id"] = str(attempt.pk)
        self.event.save()
        claim = claim_next_outbox_event()
        self.assertIsNotNone(claim)
        attempt.refresh_from_db()
        self.event.refresh_from_db()
        self.assertEqual(attempt.status, DOIRegistrationAttempt.Status.FAILED)
        self.assertEqual(self.event.status, OutboxEvent.Status.PROCESSING)

    def test_registration_history_redacts_secrets(self):
        attempt = DOIRegistrationAttempt.objects.create(
            identifier=self.identifier,
            attempt_number=1,
            request_summary={
                "scheme": "doi",
                "client_secret": "never-store-this",
                "note": "token=also-secret",
            },
            response_summary={"Authorization": "Bearer credential"},
            error_information="api_key=provider-secret",
        )
        attempt.refresh_from_db()
        self.assertNotIn("client_secret", attempt.request_summary)
        self.assertNotIn("never-store-this", str(attempt.request_summary))
        self.assertIn("[REDACTED]", attempt.request_summary["note"])
        self.assertEqual(attempt.response_summary, {})
        self.assertNotIn("provider-secret", attempt.error_information)

    @override_settings(ORDP_DOI_REGISTRATION_ENABLED=False)
    def test_management_command_does_not_contact_provider_when_disabled(self):
        output = StringIO()
        call_command("process_pid_outbox", stdout=output)
        self.event.refresh_from_db()
        self.assertEqual(self.event.status, OutboxEvent.Status.PENDING)
        self.assertEqual(self.identifier.registration_attempts.count(), 0)
        self.assertIn("disabled", output.getvalue())


class PIDWithdrawalAndAPITests(TestCase):
    def setUp(self):
        self.verify_patcher = patch(
            "apps.datasets.services.publication.verify_stored_object_in_storage"
        )
        self.verify_patcher.start()
        self.addCleanup(self.verify_patcher.stop)
        self.owner = make_user("pidapiowner", "pidapiowner@aastu.edu.et")
        self.dataset = _published_dataset(self.owner)
        self.client = APIClient()
        self.client.force_authenticate(self.owner)
    def make_outbox_fixture(self):
        identifier = ResourceIdentifier.objects.create(
            dataset=self.dataset,
            role=ResourceIdentifier.Role.CONCEPT,
            provider="mock",
            registration_status=ResourceIdentifier.RegistrationStatus.PENDING,
        )
        event = OutboxEvent.objects.create(
            event_type="pid.register_identifier",
            payload={"identifier_id": str(identifier.pk)},
            idempotency_key=f"test-pid-api:{identifier.pk}",
        )
        return identifier, event
    def test_withdrawal_preserves_registered_identifier_and_concept(self):
        release = publish_initial_dataset(self.dataset, self.owner)
        concept, version = ResourceIdentifier.objects.filter(
            scheme="doi"
        ).order_by("role")
        _mark_registered(version)
        withdraw_release(release, self.owner, "withdrawn for test")
        concept.refresh_from_db()
        version.refresh_from_db()
        self.assertEqual(concept.lifecycle_status, ResourceIdentifier.LifecycleStatus.ACTIVE)
        self.assertEqual(version.lifecycle_status, ResourceIdentifier.LifecycleStatus.WITHDRAWN)
        self.assertEqual(
            version.registration_status,
            ResourceIdentifier.RegistrationStatus.REGISTERED,
        )
        self.assertEqual(version.identifier_value, "10.5555/provider-assigned-test-value")

    def test_withdrawn_unregistered_release_cancels_registration_event(self):
        release = publish_initial_dataset(self.dataset, self.owner)
        version = ResourceIdentifier.objects.get(release=release, scheme="doi")
        withdraw_release(release, self.owner, "withdrawn before registration")
        version.refresh_from_db()
        event = OutboxEvent.objects.get(
            idempotency_key=f"pid-register:{version.pk}"
        )
        self.assertEqual(
            version.registration_status,
            ResourceIdentifier.RegistrationStatus.CANCELLED,
        )
        self.assertEqual(event.status, OutboxEvent.Status.CANCELLED)

    def test_api_hides_official_link_for_pending_identifier(self):
        ResourceIdentifier.objects.create(
            dataset=self.dataset,
            role=ResourceIdentifier.Role.CONCEPT,
            registration_status=ResourceIdentifier.RegistrationStatus.PENDING,
        )
        response = self.client.get(f"/api/datasets/{self.dataset.pk}/identifiers/")
        self.assertEqual(response.status_code, 200)
        doi_item = _doi_payload(response)
        self.assertIsNone(doi_item["official_doi_link"])
        self.assertEqual(doi_item["registration_status"], "pending")

    def test_api_exposes_official_link_only_for_registered_identifier(self):
        ResourceIdentifier.objects.create(
            dataset=self.dataset,
            role=ResourceIdentifier.Role.CONCEPT,
        )
        _mark_registered(ResourceIdentifier.objects.get(dataset=self.dataset, scheme="doi"))
        response = self.client.get(f"/api/datasets/{self.dataset.pk}/identifiers/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            _doi_payload(response)["official_doi_link"],
            "https://doi.org/10.5555/provider-assigned-test-value",
        )

    def test_api_unassigned_identifier_is_not_a_doi(self):
        ResourceIdentifier.objects.create(
            dataset=self.dataset,
            role=ResourceIdentifier.Role.CONCEPT,
        )
        response = self.client.get(f"/api/datasets/{self.dataset.pk}/identifiers/")
        doi_item = _doi_payload(response)
        self.assertIsNone(doi_item["identifier_value"])
        self.assertIsNone(doi_item["official_doi_link"])

    def test_api_exposes_withdrawn_lifecycle_separately(self):
        release = publish_initial_dataset(self.dataset, self.owner)
        withdraw_release(release, self.owner, "withdrawn for API test")
        response = self.client.get(f"/api/datasets/{self.dataset.pk}/identifiers/")
        version = next(
            item for item in response.data
            if item["role"] == "version" and item.get("scheme") == "doi"
        )
        self.assertEqual(version["lifecycle_status"], "withdrawn")
        self.assertEqual(version["official_doi_link"], None)

    def test_registered_doi_remains_visible_after_release_withdrawal(self):
        release = publish_initial_dataset(self.dataset, self.owner)
        version = ResourceIdentifier.objects.get(release=release, scheme="doi")
        _mark_registered(version)
        withdraw_release(release, self.owner, "withdrawn after registration")
        response = self.client.get(f"/api/datasets/{self.dataset.pk}/identifiers/")
        version_payload = next(
            item for item in response.data
            if item["role"] == "version" and item.get("scheme") == "doi"
        )
        self.assertEqual(version_payload["lifecycle_status"], "withdrawn")
        self.assertEqual(
            version_payload["official_doi_link"],
            "https://doi.org/10.5555/provider-assigned-test-value",
        )

    @override_settings(ORDP_DOI_REGISTRATION_ENABLED=True)
    def test_permanent_provider_error_is_not_automatically_retried(self):
        self.identifier, self.event = self.make_outbox_fixture()
        claim = claim_next_outbox_event()
        outcome = process_claimed_event(
            *claim,
            provider=MockDOIProvider(errors={
                self.identifier.pk: PermanentDOIProviderError("invalid provider request"),
            }),
        )
        self.assertEqual(outcome, OutboxEvent.Status.FAILED)
        self.event.refresh_from_db()
        self.assertTrue(self.event.payload.get("_permanent_failure"))
        with self.assertRaises(ValidationError):
            retry_failed_outbox_event(self.event.pk)

    @override_settings(ORDP_DOI_REGISTRATION_ENABLED=True)
    def test_operator_override_allows_one_audited_retry_after_permanent_failure(self):
        self.identifier, self.event = self.make_outbox_fixture()
        self.event.status = OutboxEvent.Status.FAILED
        self.event.retry_count = MAX_REGISTRATION_ATTEMPTS
        self.event.payload = {**self.event.payload, "_permanent_failure": True}
        self.event.last_error = "invalid provider configuration"
        self.event.failed_at = timezone.now()
        self.event.save()
        self.identifier.registration_status = ResourceIdentifier.RegistrationStatus.FAILED
        self.identifier.save(update_fields=["registration_status", "updated_at"])

        clear_permanent_failure_and_retry(
            self.event.pk, reason="Provider configuration corrected by operator"
        )
        claim = claim_next_outbox_event()
        result = RegistrationResult(identifier_value="10.5555/manual-retry-value")
        outcome = process_claimed_event(
            *claim,
            provider=MockDOIProvider(responses={self.identifier.pk: result}),
        )
        self.assertEqual(outcome, "completed")
        self.event.refresh_from_db()
        self.assertEqual(self.event.status, OutboxEvent.Status.COMPLETED)
        self.assertEqual(self.event.retry_count, MAX_REGISTRATION_ATTEMPTS + 1)

    def test_terminal_registration_attempt_cannot_be_changed_or_deleted(self):
        self.identifier, self.event = self.make_outbox_fixture()
        attempt = DOIRegistrationAttempt.objects.create(
            identifier=self.identifier, attempt_number=1,
        )
        attempt.status = DOIRegistrationAttempt.Status.FAILED
        attempt.error_information = "temporary failure"
        attempt.save(update_fields=["status", "error_information"])
        attempt.status = DOIRegistrationAttempt.Status.SUCCEEDED
        with self.assertRaises(RuntimeError):
            attempt.save(update_fields=["status"])
        with self.assertRaises(Exception):
            attempt.delete()


class PostgreSQLPIDOutboxClaimTests(TransactionTestCase):
    reset_sequences = True

    @skipUnlessDBFeature("has_select_for_update_skip_locked")
    def test_concurrent_workers_claim_distinct_events(self):
        owner = make_user("pidlockowner", "pidlockowner@aastu.edu.et")
        dataset = Dataset.objects.create(title="Concurrent outbox", owner=owner)
        claims = []

        for index in range(2):
            identifier = ResourceIdentifier.objects.create(
                dataset=dataset,
                role=ResourceIdentifier.Role.CONCEPT if index == 0 else ResourceIdentifier.Role.VERSION,
                release=None,
                registration_status=ResourceIdentifier.RegistrationStatus.PENDING,
            ) if index == 0 else None
            if index == 0:
                OutboxEvent.objects.create(
                    event_type="pid.register_identifier",
                    payload={"identifier_id": str(identifier.pk)},
                    idempotency_key=f"concurrent-pid:{index}",
                )
            else:
                release = DatasetRelease.objects.create(
                    dataset=dataset,
                    major=1,
                    minor=0,
                    patch=0,
                    status=DatasetRelease.Status.DRAFT,
                    metadata_snapshot={"schema_version": 1},
                )
                version = ResourceIdentifier.objects.create(
                    release=release,
                    role=ResourceIdentifier.Role.VERSION,
                    registration_status=ResourceIdentifier.RegistrationStatus.PENDING,
                )
                OutboxEvent.objects.create(
                    event_type="pid.register_identifier",
                    payload={"identifier_id": str(version.pk)},
                    idempotency_key=f"concurrent-pid:{index}",
                )

        def claim():
            close_old_connections()
            claims.append(claim_next_outbox_event())
            close_old_connections()

        threads = [Thread(target=claim) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        self.assertTrue(all(not thread.is_alive() for thread in threads))
        claimed_ids = [claim[0] for claim in claims if claim]
        self.assertEqual(len(claimed_ids), 2)
        self.assertEqual(len(set(claimed_ids)), 2)
        self.assertEqual(connection.vendor, "postgresql")


class PIDHistoricalReleaseBackfillTests(TestCase):
    def test_existing_releases_are_backfilled_idempotently_without_identifier_values(self):
        owner = make_user("pidmigrationowner", "pidmigrationowner@aastu.edu.et")
        dataset = Dataset.objects.create(title="Historical PID", owner=owner)
        releases = []
        for patch_number in (0, 1):
            release = DatasetRelease.objects.create(
                dataset=dataset,
                major=1,
                minor=0,
                patch=patch_number,
                status=DatasetRelease.Status.DRAFT,
                snapshot_schema_version=1,
                metadata_snapshot={"schema_version": 1, "title": dataset.title},
                citation_snapshot={"title": dataset.title},
                license_snapshot={"name": "unknown"},
            )
            stored = _stored_object(dataset, f"historic-{patch_number}.csv")
            from apps.datasets.models import DatasetReleaseFile

            DatasetReleaseFile.objects.create(
                release=release,
                stored_object=stored,
                logical_file_id=uuid.uuid4(),
            )
            release.status = DatasetRelease.Status.PUBLISHED
            release.save(update_fields=["status"])
            releases.append(release)
        releases[1].status = DatasetRelease.Status.WITHDRAWN
        releases[1].withdrawn_at = timezone.now()
        releases[1].withdrawal_reason = "historical test withdrawal"
        releases[1].save(update_fields=[
            "status", "withdrawn_at", "withdrawal_reason",
        ])

        migration = import_module("apps.datasets.migrations.0032_pid_doi_outbox")
        migration.create_identifiers_for_existing_releases(django_apps, None)
        migration.create_identifiers_for_existing_releases(django_apps, None)

        identifiers = ResourceIdentifier.objects.filter(
            dataset=dataset, scheme="doi"
        ) | ResourceIdentifier.objects.filter(release__dataset=dataset, scheme="doi")
        self.assertEqual(identifiers.count(), 3)
        self.assertFalse(identifiers.exclude(identifier_value__isnull=True).exists())
        self.assertEqual(
            ResourceIdentifier.objects.get(release=releases[1], scheme="doi").lifecycle_status,
            ResourceIdentifier.LifecycleStatus.WITHDRAWN,
        )
        self.assertEqual(OutboxEvent.objects.count(), 2)


class PostgreSQLPIDGuardTests(TestCase):
    def setUp(self):
        self.owner = make_user("pidguardowner", "pidguardowner@aastu.edu.et")
        self.dataset = Dataset.objects.create(title="PID guards", owner=self.owner)
        self.release = DatasetRelease.objects.create(
            dataset=self.dataset,
            major=1,
            minor=0,
            patch=0,
            status=DatasetRelease.Status.DRAFT,
            metadata_snapshot={"schema_version": 1},
        )
        self.concept = ResourceIdentifier.objects.create(
            dataset=self.dataset,
            role=ResourceIdentifier.Role.CONCEPT,
        )
        self.version = ResourceIdentifier.objects.create(
            release=self.release,
            role=ResourceIdentifier.Role.VERSION,
        )

    def _require_postgresql(self):
        if connection.vendor != "postgresql":
            self.skipTest("PostgreSQL trigger verification requires PostgreSQL.")

    def test_raw_sql_cannot_delete_identifier_history(self):
        self._require_postgresql()
        with self.assertRaises(DatabaseError):
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute(
                        "DELETE FROM datasets_resourceidentifier WHERE id = %s",
                        [self.concept.pk],
                    )

    def test_raw_sql_cannot_change_identifier_target(self):
        self._require_postgresql()
        with self.assertRaises(DatabaseError):
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute(
                        "UPDATE datasets_resourceidentifier SET release_id = %s "
                        "WHERE id = %s",
                        [self.release.pk, self.concept.pk],
                    )

    def test_raw_sql_cannot_claim_registration_without_successful_attempt(self):
        self._require_postgresql()
        with self.assertRaises(DatabaseError):
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute(
                        "UPDATE datasets_resourceidentifier SET "
                        "identifier_value = %s, registration_status = 'registered', "
                        "registered_at = %s WHERE id = %s",
                        [
                            "10.5555/unverified-test-value",
                            timezone.now(),
                            self.concept.pk,
                        ],
                    )

    def test_raw_sql_cannot_delete_registration_attempt_history(self):
        self._require_postgresql()
        attempt = DOIRegistrationAttempt.objects.create(
            identifier=self.concept, attempt_number=1,
        )
        with self.assertRaises(DatabaseError):
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute(
                        "DELETE FROM datasets_doiregistrationattempt WHERE id = %s",
                        [attempt.pk],
                    )

    def test_raw_sql_cannot_mutate_pid_transition_audit(self):
        self._require_postgresql()
        event = OutboxEvent.objects.create(
            event_type="pid.register_identifier",
            payload={"identifier_id": str(self.concept.pk)},
            idempotency_key=f"audit-guard:{self.concept.pk}",
        )
        audit = PIDRegistrationEvent.objects.get(event_id=event.pk)
        with self.assertRaises(DatabaseError):
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute(
                        "UPDATE datasets_pidregistrationevent SET detail = 'tampered' WHERE id = %s",
                        [audit.pk],
                    )

    def test_raw_sql_cannot_insert_relationship_with_invalid_role_direction(self):
        self._require_postgresql()
        with self.assertRaises(DatabaseError):
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute(
                        "INSERT INTO datasets_resourcerelationship "
                        "(id, subject_identifier_id, related_identifier_id, "
                        "relationship_type, created_at) VALUES (%s, %s, %s, %s, %s)",
                        [
                            uuid.uuid4(),
                            self.version.pk,
                            self.concept.pk,
                            ResourceRelationship.Type.HAS_VERSION,
                            timezone.now(),
                        ],
                    )
