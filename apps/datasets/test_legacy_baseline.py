import uuid
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from apps.datasets.factories import make_user
from apps.datasets.models import Dataset, DatasetFile, DatasetVersion, StoredObject
from apps.datasets.services.legacy_baseline import backfill_legacy_baseline_releases


class LegacyBaselineBackfillTests(TestCase):
    def setUp(self):
        self.owner = make_user("baseline-owner", "baseline-owner@aastu.edu.et")
        self.dataset = Dataset.objects.create(
            title="Legacy baseline", owner=self.owner, status=Dataset.Status.PUBLISHED
        )
        self.object = StoredObject.objects.create(
            dataset=self.dataset,
            storage_key=f"objects/{self.dataset.pk}/{uuid.uuid4()}/legacy.csv",
            original_filename="legacy.csv",
            file_type="csv",
            file_size=5,
            checksum_sha256="a" * 64,
            verified_at=timezone.now(),
        )
        DatasetFile.objects.create(
            dataset=self.dataset,
            file_key=self.object.storage_key,
            original_filename="legacy.csv",
            file_type="csv",
            file_size=5,
            checksum="a" * 64,
            stored_object=self.object,
        )
        DatasetVersion.objects.create(
            dataset=self.dataset,
            version_number=4,
            file_key=self.object.storage_key,
            source=DatasetVersion.Source.OWNER_EDIT,
            changed_by=self.owner,
            change_summary={"legacy": "preserve"},
            diff_percentage=20,
        )

    @patch("apps.datasets.services.legacy_baseline.verify_stored_object_in_storage")
    @patch("apps.datasets.services.publication.verify_stored_object_in_storage")
    def test_dry_run_reports_baseline_without_creating_release(self, verify_publish, verify_backfill):
        result = backfill_legacy_baseline_releases(dry_run=True)
        self.assertEqual(result["would_create_count"], 1)
        self.assertEqual(self.dataset.releases.count(), 0)
        verify_backfill.assert_called_once()
        verify_publish.assert_not_called()

    @patch("apps.datasets.services.legacy_baseline.verify_stored_object_in_storage")
    @patch("apps.datasets.services.publication.verify_stored_object_in_storage")
    def test_verified_legacy_dataset_gets_one_point_zero_zero(self, verify_publish, verify_backfill):
        result = backfill_legacy_baseline_releases()
        self.assertEqual(result["created_count"], 1)
        self.dataset.refresh_from_db()
        self.assertEqual(self.dataset.published_release.version, "1.0.0")
        self.assertEqual(self.dataset.versions.count(), 1)
        self.assertEqual(self.dataset.versions.first().version_number, 4)
        self.assertEqual(self.dataset.releases.count(), 1)

    def test_unlinked_legacy_file_is_reported_as_unverifiable(self):
        DatasetFile.objects.filter(dataset=self.dataset).update(stored_object=None)
        result = backfill_legacy_baseline_releases(dry_run=True)
        self.assertEqual(result["unverifiable_dataset_ids"], [str(self.dataset.pk)])
        self.assertEqual(self.dataset.releases.count(), 0)
