
from unittest.mock import patch
from io import StringIO

from django.core.management import call_command
from django.test import TestCase, override_settings

from rest_framework.test import APIClient

from apps.datasets.factories import make_user
from apps.datasets.models import (
    Dataset,
    ResourceIdentifier,
    ResourceRelationship,
    OutboxEvent,
)
from apps.datasets.services.publication import (
    publish_initial_dataset,
    withdraw_release,
)
from apps.datasets.test_pid import _published_dataset


@override_settings(
    ORDP_DOI_REGISTRATION_POLICY="disabled",
    ORDP_DOI_REGISTRATION_ENABLED=False,
    ORDP_PUBLIC_BASE_URL="https://pid.example.test",
    ORDP_FRONTEND_BASE_URL="",
)
class ORDPIndependentPIDTests(TestCase):

    def setUp(self):
        self.verify_patcher = patch(
            "apps.datasets.services.publication.verify_stored_object_in_storage"
        )
        self.verify_patcher.start()
        self.addCleanup(self.verify_patcher.stop)

        self.owner = make_user(
            "ordppidowner",
            "ordppidowner@example.test",
        )
        self.dataset = _published_dataset(self.owner)

    def publish(self):
        return publish_initial_dataset(self.dataset, self.owner)

    def test_doi_disabled_still_creates_concept_and_version_pids(self):
        release = self.publish()

        concept = ResourceIdentifier.objects.get(
            scheme=ResourceIdentifier.Scheme.ORDP,
            dataset=self.dataset,
            role=ResourceIdentifier.Role.CONCEPT,
        )

        version = ResourceIdentifier.objects.get(
            scheme=ResourceIdentifier.Scheme.ORDP,
            release=release,
            role=ResourceIdentifier.Role.VERSION,
        )

        self.assertTrue(
            concept.identifier_value.startswith("ORDP-DS-")
        )
        self.assertTrue(
            version.identifier_value.startswith("ORDP-REL-")
        )

        self.assertEqual(
            concept.registration_status,
            ResourceIdentifier.RegistrationStatus.NOT_REQUESTED,
        )
        self.assertEqual(
            version.registration_status,
            ResourceIdentifier.RegistrationStatus.NOT_REQUESTED,
        )

        self.assertEqual(OutboxEvent.objects.count(), 0)

    def test_pid_values_are_stable_and_relationships_are_provider_independent(self):
        release = self.publish()

        concept = ResourceIdentifier.objects.get(
            scheme=ResourceIdentifier.Scheme.ORDP,
            dataset=self.dataset,
            role=ResourceIdentifier.Role.CONCEPT,
        )

        version = ResourceIdentifier.objects.get(
            scheme=ResourceIdentifier.Scheme.ORDP,
            release=release,
            role=ResourceIdentifier.Role.VERSION,
        )

        self.assertTrue(
            ResourceRelationship.objects.filter(
                subject_identifier=concept,
                related_identifier=version,
                relationship_type=ResourceRelationship.Type.HAS_VERSION,
            ).exists()
        )

        original_pid = concept.identifier_value

        self.dataset.title = "Changed title"
        self.dataset.save(update_fields=["title"])

        concept.refresh_from_db()

        self.assertEqual(concept.identifier_value, original_pid)

    def test_public_resolver_returns_exact_resource_and_citation_without_doi(self):
        self.dataset.visibility = Dataset.Visibility.PUBLIC
        self.dataset.save(update_fields=["visibility"])

        release = self.publish()

        version = ResourceIdentifier.objects.get(
            scheme=ResourceIdentifier.Scheme.ORDP,
            release=release,
            role=ResourceIdentifier.Role.VERSION,
        )

        client = APIClient()

        # API resolver.
        api_response = client.get(
            f"/api/datasets/id/{version.identifier_value}/"
        )

        self.assertEqual(api_response.status_code, 200)
        self.assertEqual(api_response.data["resource_type"], "release")
        self.assertEqual(
            api_response.data["release_id"],
            str(release.pk),
        )
        self.assertIsNone(api_response.data["citation"]["doi"])

        # Public resolver is JSON too. It does not render an HTML template.
        public_response = client.get(
            f"/id/{version.identifier_value}/"
        )

        self.assertEqual(public_response.status_code, 200)
        self.assertEqual(
            public_response.data["resource_type"],
            "release",
        )
        self.assertEqual(
            public_response.data["release_id"],
            str(release.pk),
        )
        self.assertEqual(
            public_response.data["pid"]["identifier"],
            version.identifier_value,
        )
        self.assertIsNone(
            public_response.data["citation"]["doi"]
        )

    def test_backfill_dry_run_and_execution_are_idempotent(self):
        release = self.publish()

        output = StringIO()

        call_command(
            "backfill_pid_identifiers",
            dataset_id=str(self.dataset.pk),
            dry_run=True,
            stdout=output,
        )

        self.assertIn("Dry run", output.getvalue())

        call_command(
            "backfill_pid_identifiers",
            dataset_id=str(self.dataset.pk),
        )

        self.assertEqual(
            ResourceIdentifier.objects.filter(
                scheme=ResourceIdentifier.Scheme.ORDP,
                dataset=self.dataset,
                role=ResourceIdentifier.Role.CONCEPT,
            ).count(),
            1,
        )

        self.assertEqual(
            ResourceIdentifier.objects.filter(
                scheme=ResourceIdentifier.Scheme.ORDP,
                release=release,
                role=ResourceIdentifier.Role.VERSION,
            ).count(),
            1,
        )

    def test_withdrawal_retains_the_version_pid(self):
        release = self.publish()

        version = ResourceIdentifier.objects.get(
            scheme=ResourceIdentifier.Scheme.ORDP,
            release=release,
            role=ResourceIdentifier.Role.VERSION,
        )

        original_pid = version.identifier_value

        withdraw_release(
            release,
            self.owner,
            "withdrawn in test",
        )

        version.refresh_from_db()

        self.assertEqual(
            version.identifier_value,
            original_pid,
        )
        self.assertEqual(
            version.lifecycle_status,
            ResourceIdentifier.LifecycleStatus.WITHDRAWN,
        )
        self.assertEqual(
            version.registration_status,
            ResourceIdentifier.RegistrationStatus.NOT_REQUESTED,
        )

    def test_private_dataset_pid_is_not_discoverable_to_anonymous_visitor(self):
        release = self.publish()

        version = ResourceIdentifier.objects.get(
            scheme=ResourceIdentifier.Scheme.ORDP,
            release=release,
            role=ResourceIdentifier.Role.VERSION,
        )

        response = APIClient().get(
            f"/id/{version.identifier_value}/"
        )

        self.assertEqual(response.status_code, 404)
