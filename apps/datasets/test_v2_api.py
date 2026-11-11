import uuid
from django.test import override_settings
from django.utils import timezone
from unittest.mock import patch
from rest_framework import status
from rest_framework.test import APITestCase

from apps.datasets.factories import make_user
from apps.datasets.serializers import DatasetSerializer
from apps.datasets.models import (
    Dataset, DatasetFile, DatasetRelease, DatasetReleaseFile, StoredObject,
    ContributorAttribution,
)
from apps.metadata.models import Category, Metadata
from apps.datasets.services.storage import StorageVerificationError


class V2ReleaseAndMetadataAuditAPITests(APITestCase):
    def setUp(self):
        self.owner = make_user("v2-api-owner", "v2-api-owner@aastu.edu.et")
        self.dataset = Dataset.objects.create(
            title="V2 API dataset", owner=self.owner,
            status=Dataset.Status.PUBLISHED,
        )
        category = Category.objects.create(name="V2 API category")
        self.metadata = Metadata.objects.create(
            dataset=self.dataset, description="Before", category=category
        )
        self.object = StoredObject.objects.create(
            dataset=self.dataset,
            storage_key=f"objects/{self.dataset.pk}/{uuid.uuid4()}/api.csv",
            original_filename="api.csv", file_type="csv", file_size=3,
            checksum_sha256="a" * 64,
        )
        self.file = DatasetFile.objects.create(
            dataset=self.dataset, file_key=self.object.storage_key,
            original_filename="api.csv", file_type="csv", file_size=3,
            checksum="a" * 64, stored_object=self.object,
        )
        self.release = DatasetRelease.objects.create(
            dataset=self.dataset, major=1, minor=0, patch=0,
            metadata_snapshot={
                "schema_version": 1, "title": self.dataset.title,
                "description": "Published snapshot description", "category": {"id": str(category.pk), "name": category.name, "description": ""},
                "creator": {"name": "V2 Api Owner", "affiliation": "AASTU"},
                "contributors": [], "keywords": [], "languages": [], "characteristics": [],
                "coverage": {"geographic": "", "temporal": ""},
                "methodology": {}, "citation": {}, "resources": {"related_resources": ""},
            },
        )
        DatasetReleaseFile.objects.create(
            release=self.release, stored_object=self.object,
            logical_file_id=self.file.pk,
        )
        self.release.status = DatasetRelease.Status.PUBLISHED
        self.release.save(update_fields=["status"])
        self.dataset.published_release = self.release
        self.dataset.save(update_fields=["published_release"])
        self.client.force_authenticate(self.owner)

    def test_safe_metadata_correction_creates_before_after_audit(self):
        response = self.client.patch(
            f"/api/datasets/{self.dataset.pk}/update/",
            {"description": "Corrected description"}, format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        audit = self.dataset.metadata_edit_audits.get()
        self.assertEqual(audit.before_snapshot["description"], "Published snapshot description")
        self.assertEqual(audit.after_snapshot["description"], "Corrected description")
        self.assertEqual(DatasetSerializer(self.dataset).data["description"], "Corrected description")

    def test_title_change_requires_a_new_release_proposal(self):
        response = self.client.patch(
            f"/api/datasets/{self.dataset.pk}/update/",
            {"title": "Changed title"}, format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.dataset.refresh_from_db()
        self.assertEqual(self.dataset.title, "V2 API dataset")

    def test_dataset_serializer_reads_current_v2_metadata_from_release_snapshot(self):
        data = DatasetSerializer(self.dataset).data
        self.assertEqual(data["description"], "Published snapshot description")
        self.assertEqual(data["version"], "1.0.0")
        self.assertEqual(data["metadata"]["description"], "Published snapshot description")

    def test_release_history_exposes_v2_release(self):
        response = self.client.get(f"/api/datasets/{self.dataset.pk}/releases/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data[0]["version"], "1.0.0")

    def test_release_history_exposes_release_specific_contributor_attributions(self):
        ContributorAttribution.objects.create(
            dataset=self.dataset,
            release=self.release,
            user=self.owner,
            name="V2 API Owner",
            affiliation="AASTU",
            contribution_description="Prepared the published revision.",
        )

        response = self.client.get(f"/api/datasets/{self.dataset.pk}/releases/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        attribution = response.data[0]["attributions"][0]
        self.assertEqual(attribution["name"], "V2 API Owner")
        self.assertEqual(attribution["affiliation"], "AASTU")
        self.assertEqual(
            attribution["contribution_description"],
            "Prepared the published revision.",
        )
        self.assertNotIn("user", attribution)

    def test_existing_versions_endpoint_returns_v2_releases(self):
        response = self.client.get(f"/api/datasets/{self.dataset.pk}/versions/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data[0]["version"], "1.0.0")

    @override_settings(ORDP_DATACITE_PUBLISHER="Open Research Data Platform")
    def test_pid_metadata_preview_is_provider_independent_and_does_not_forge_doi(self):
        from apps.datasets.models import ResourceIdentifier
        identifier = ResourceIdentifier.objects.create(
            dataset=self.dataset,
            role=ResourceIdentifier.Role.CONCEPT,
            landing_page_url=f"https://example.test/datasets/{self.dataset.pk}",
            registration_status=ResourceIdentifier.RegistrationStatus.PENDING,
        )
        response = self.client.get(
            f"/api/datasets/{self.dataset.pk}/identifiers/{identifier.pk}/datacite-metadata/"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["ready"])
        self.assertEqual(response.data["attributes"]["version"], "1.0.0")
        self.assertNotIn("doi", response.data["attributes"])

    @override_settings(ORDP_DATACITE_PUBLISHER="Open Research Data Platform")
    def test_datacite_preview_maps_registered_relationships_to_canonical_types(self):
        from apps.datasets.models import (
            DOIRegistrationAttempt, ResourceIdentifier, ResourceRelationship,
        )
        concept = ResourceIdentifier.objects.create(
            dataset=self.dataset, role=ResourceIdentifier.Role.CONCEPT,
            landing_page_url=f"https://example.test/datasets/{self.dataset.pk}",
        )
        version = ResourceIdentifier.objects.create(
            release=self.release, role=ResourceIdentifier.Role.VERSION,
        )
        attempt = DOIRegistrationAttempt.objects.create(identifier=version, attempt_number=1)
        attempt.status = DOIRegistrationAttempt.Status.SUCCEEDED
        attempt.save(update_fields=["status"])
        version.identifier_value = "10.5555/registered-version"
        version.registration_status = ResourceIdentifier.RegistrationStatus.REGISTERED
        version.registered_at = timezone.now()
        version.save(update_fields=["identifier_value", "registration_status", "registered_at", "updated_at"])
        ResourceRelationship.objects.create(
            subject_identifier=concept, related_identifier=version,
            relationship_type=ResourceRelationship.Type.HAS_VERSION,
        )
        response = self.client.get(
            f"/api/datasets/{self.dataset.pk}/identifiers/{concept.pk}/datacite-metadata/"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            response.data["attributes"]["relatedIdentifiers"][0]["relationType"],
            "HasVersion",
        )
        self.assertEqual(
            response.data["attributes"]["relatedIdentifiers"][0]["relatedIdentifier"],
            "10.5555/registered-version",
        )

    def test_release_detail_exposes_datacite_identifier_relationships(self):
        from apps.datasets.models import ResourceIdentifier, ResourceRelationship
        concept = ResourceIdentifier.objects.create(
            dataset=self.dataset, role=ResourceIdentifier.Role.CONCEPT,
            registration_status=ResourceIdentifier.RegistrationStatus.PENDING,
        )
        version = ResourceIdentifier.objects.create(
            release=self.release, role=ResourceIdentifier.Role.VERSION,
            registration_status=ResourceIdentifier.RegistrationStatus.PENDING,
        )
        ResourceRelationship.objects.create(
            subject_identifier=concept, related_identifier=version,
            relationship_type=ResourceRelationship.Type.HAS_VERSION,
        )
        response = self.client.get(
            f"/api/datasets/{self.dataset.pk}/releases/{self.release.pk}/"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            response.data["identifier_relationships"][0]["relationship_type"],
            "hasVersion",
        )


class DatasetIdentifierVisibilityAPITests(APITestCase):
    def setUp(self):
        from apps.datasets.models import ResourceIdentifier
        self.owner = make_user("identifier-owner", "identifier-owner@aastu.edu.et")
        self.other = make_user("identifier-outsider", "identifier-outsider@aastu.edu.et")
        self.dataset = Dataset.objects.create(
            title="Restricted identifier dataset", owner=self.owner,
            status=Dataset.Status.PUBLISHED,
            visibility=Dataset.Visibility.RESTRICTED,
        )
        ResourceIdentifier.objects.create(
            dataset=self.dataset,
            scheme="doi",
            role=ResourceIdentifier.Role.CONCEPT,
            provider="unconfigured",
            registration_status=ResourceIdentifier.RegistrationStatus.NOT_REQUESTED,
        )

    def test_unauthorized_user_cannot_read_restricted_identifiers(self):
        self.client.force_authenticate(self.other)
        response = self.client.get(f"/api/datasets/{self.dataset.pk}/identifiers/")
        self.assertEqual(response.status_code, 404)
        self.assertNotIn("identifier_value", str(response.data))

    def test_owner_can_read_restricted_identifiers(self):
        self.client.force_authenticate(self.owner)
        response = self.client.get(f"/api/datasets/{self.dataset.pk}/identifiers/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)

    def test_coowner_can_read_restricted_identifiers(self):
        from apps.datasets.models import Contributor
        coowner = make_user("identifier-coowner", "identifier-coowner@aastu.edu.et")
        Contributor.objects.create(
            dataset=self.dataset, user=coowner,
            contributor_type=Contributor.ContributorType.OWNER,
        )
        self.client.force_authenticate(coowner)
        response = self.client.get(f"/api/datasets/{self.dataset.pk}/identifiers/")
        self.assertEqual(response.status_code, 200)

    def test_authorized_share_collaborator_can_read_restricted_identifiers(self):
        from apps.sharing.models import SharePermission
        collaborator = make_user("identifier-shared", "identifier-shared@aastu.edu.et")
        SharePermission.objects.create(
            dataset=self.dataset, shared_with_user=collaborator,
            access_type=SharePermission.AccessType.VIEW,
            status=SharePermission.Status.ACTIVE,
        )
        self.client.force_authenticate(collaborator)
        response = self.client.get(f"/api/datasets/{self.dataset.pk}/identifiers/")
        self.assertEqual(response.status_code, 200)

    def test_private_identifier_access_matches_private_version_access(self):
        self.dataset.visibility = Dataset.Visibility.PRIVATE
        self.dataset.save(update_fields=["visibility"])
        self.client.force_authenticate(self.other)
        denied = self.client.get(f"/api/datasets/{self.dataset.pk}/identifiers/")
        self.assertEqual(denied.status_code, 404)
        self.client.force_authenticate(self.owner)
        allowed = self.client.get(f"/api/datasets/{self.dataset.pk}/identifiers/")
        self.assertEqual(allowed.status_code, 200)


class ReleaseFileDownloadAPITests(APITestCase):
    def setUp(self):
        self.owner = make_user("release-download-owner", "release-download-owner@aastu.edu.et")
        self.other = make_user("release-download-outsider", "release-download-outsider@aastu.edu.et")
        self.dataset = Dataset.objects.create(
            title="Release download dataset", owner=self.owner,
            status=Dataset.Status.PUBLISHED,
        )
        self.object = StoredObject.objects.create(
            dataset=self.dataset,
            storage_key=f"objects/{self.dataset.pk}/{uuid.uuid4()}/release.csv",
            original_filename="release.csv", file_type="csv", file_size=3,
            checksum_sha256="a" * 64,
        )
        self.release = DatasetRelease.objects.create(
            dataset=self.dataset, major=1, minor=0, patch=0,
            metadata_snapshot={"schema_version": 1},
        )
        self.logical_file_id = uuid.uuid4()
        DatasetReleaseFile.objects.create(
            release=self.release, stored_object=self.object,
            logical_file_id=self.logical_file_id,
        )
        self.release.status = DatasetRelease.Status.PUBLISHED
        self.release.save(update_fields=["status"])

    @patch("apps.datasets.services.file_integrity.open_verified_object_for_download")
    def test_release_file_download_uses_selected_release_manifest(self, _open_verified):
        from io import BytesIO
        _open_verified.return_value = BytesIO(b"abc")
        self.client.force_authenticate(self.owner)
        response = self.client.get(
            f"/api/datasets/{self.dataset.pk}/releases/{self.release.pk}/files/{self.logical_file_id}/download/"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(b"".join(response.streaming_content), b"abc")
        self.assertIn("release.csv", response["Content-Disposition"])
        _open_verified.assert_called_once_with(self.object)

    def test_release_file_download_does_not_disclose_restricted_files(self):
        self.dataset.visibility = Dataset.Visibility.RESTRICTED
        self.dataset.save(update_fields=["visibility"])
        self.client.force_authenticate(self.other)
        response = self.client.get(
            f"/api/datasets/{self.dataset.pk}/releases/{self.release.pk}/files/{self.logical_file_id}/download/"
        )
        self.assertEqual(response.status_code, 404)

    def test_release_file_download_rejects_file_not_in_selected_release(self):
        self.client.force_authenticate(self.owner)
        response = self.client.get(
            f"/api/datasets/{self.dataset.pk}/releases/{self.release.pk}/files/{uuid.uuid4()}/download/"
        )
        self.assertEqual(response.status_code, 404)

    @patch(
        "apps.datasets.services.file_integrity.open_verified_object_for_download",
        side_effect=StorageVerificationError("checksum mismatch"),
    )
    def test_release_file_download_returns_unavailable_for_corrupt_object(self, _open_verified):
        self.client.force_authenticate(self.owner)
        response = self.client.get(
            f"/api/datasets/{self.dataset.pk}/releases/{self.release.pk}/files/{self.logical_file_id}/download/"
        )
        self.assertEqual(response.status_code, 503)
