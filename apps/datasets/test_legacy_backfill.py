import hashlib
import uuid
from io import StringIO
from unittest.mock import patch

from botocore.exceptions import ClientError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError
from django.test import TestCase

from apps.datasets.factories import make_user
from apps.datasets.models import Dataset, DatasetFile, DatasetRelease, DatasetReleaseFile, StoredObject
from apps.datasets.services.storage import StorageVerificationError
from apps.datasets.services.legacy_backfill import (
    _validate_storage_key,
    backfill_legacy_stored_objects,
)


class FakeBody:
    def __init__(self, content):
        self.content = content
        self.closed = False

    def iter_chunks(self, chunk_size):
        yield self.content[:chunk_size]

    def close(self):
        self.closed = True


class LegacyStoredObjectBackfillTests(TestCase):
    def setUp(self):
        owner = make_user("backfillowner", "backfillowner@aastu.edu.et")
        self.dataset = Dataset.objects.create(title="Legacy data", owner=owner)
        self.content = b"actual legacy storage bytes"
        self.file = DatasetFile.objects.create(
            dataset=self.dataset,
            file_key="legacy/data.csv",
            original_filename="data.csv",
            file_type="csv",
            file_size=len(self.content),
            checksum="legacy checksum not known to be SHA-256",
        )
        self.body = FakeBody(self.content)
        self.storage = type(
            "FakeStorage",
            (),
            {
                "get_object": lambda _self, **kwargs: {"Body": self.body},
            },
        )()

    @patch("apps.datasets.services.legacy_backfill.storage_client")
    def test_hashes_actual_bytes_before_linking_and_marks_verified(self, storage_client_mock):
        storage_client_mock.return_value = self.storage

        result = backfill_legacy_stored_objects()

        self.assertEqual(
            result,
            {
                "scanned_count": 1,
                "linked_count": 1,
                "would_link_count": 0,
                "skipped_count": 0,
                "v2_skipped_count": 0,
                "errors": [],
            },
        )
        self.file.refresh_from_db()
        stored_object = self.file.stored_object
        self.assertEqual(stored_object.checksum_sha256, hashlib.sha256(self.content).hexdigest())
        self.assertEqual(self.file.checksum, stored_object.checksum_sha256)
        self.assertIsNotNone(stored_object.verified_at)
        self.assertTrue(self.body.closed)

    @patch("apps.datasets.services.legacy_backfill.storage_client")
    def test_dry_run_does_not_create_or_link_a_stored_object(self, storage_client_mock):
        storage_client_mock.return_value = self.storage

        result = backfill_legacy_stored_objects(dry_run=True)

        self.assertEqual(result["would_link_count"], 1)
        self.assertEqual(result["linked_count"], 0)
        self.assertEqual(result["scanned_count"], 1)
        self.assertEqual(result["skipped_count"], 0)
        self.assertIsNone(DatasetFile.objects.get(pk=self.file.pk).stored_object_id)
        self.assertEqual(StoredObject.objects.count(), 0)

    @patch("apps.datasets.services.legacy_backfill.storage_client")
    def test_mismatching_legacy_sha256_is_not_linked_or_marked_verified(
        self, storage_client_mock
    ):
        storage_client_mock.return_value = self.storage
        self.file.checksum = "0" * 64
        self.file.save(update_fields=["checksum"])

        result = backfill_legacy_stored_objects()

        self.assertEqual(result["linked_count"], 0)
        self.assertEqual(len(result["errors"]), 1)
        self.assertIsNone(DatasetFile.objects.get(pk=self.file.pk).stored_object_id)
        self.assertEqual(StoredObject.objects.count(), 0)

    @patch("apps.datasets.services.legacy_backfill.storage_client")
    def test_empty_and_malformed_keys_are_rejected_before_storage_access(
        self, storage_client_mock
    ):
        for key in ("", "   ", "https://example.test/data.csv", "bad\x00key"):
            with self.subTest(key=key):
                with self.assertRaises(StorageVerificationError):
                    _validate_storage_key(key)
        storage_client_mock.assert_not_called()

    @patch("apps.datasets.services.legacy_backfill.storage_client")
    def test_duplicate_keys_are_reported_in_dry_run(self, storage_client_mock):
        DatasetFile.objects.create(
            dataset=self.dataset,
            file_key=self.file.file_key,
            original_filename="duplicate.csv",
            file_type="csv",
            file_size=self.file.file_size,
            checksum="unknown",
        )
        storage_client_mock.return_value = self.storage
        with patch.object(
            self.storage, "get_object", wraps=self.storage.get_object
        ) as get_object:
            result = backfill_legacy_stored_objects(dry_run=True)

        self.assertEqual(result["scanned_count"], 2)
        self.assertEqual(result["would_link_count"], 1)
        self.assertEqual(len(result["errors"]), 1)
        get_object.assert_called_once()
        self.assertEqual(StoredObject.objects.count(), 0)

    @patch("apps.datasets.services.legacy_backfill.storage_client")
    def test_existing_object_key_is_reported_in_dry_run(self, storage_client_mock):
        StoredObject.objects.create(
            dataset=self.dataset,
            storage_key=self.file.file_key,
            original_filename="existing.csv",
            file_type="csv",
            file_size=self.file.file_size,
            checksum_sha256=hashlib.sha256(self.content).hexdigest(),
        )

        result = backfill_legacy_stored_objects(dry_run=True)

        self.assertEqual(result["would_link_count"], 0)
        self.assertEqual(len(result["errors"]), 1)
        storage_client_mock.assert_not_called()

    @patch("apps.datasets.services.legacy_backfill.storage_client")
    def test_existing_object_from_another_dataset_is_not_reused(
        self, storage_client_mock
    ):
        other_owner = make_user("backfillother", "backfillother@example.com")
        other_dataset = Dataset.objects.create(title="Other legacy data", owner=other_owner)
        StoredObject.objects.create(
            dataset=other_dataset,
            storage_key=self.file.file_key,
            original_filename="foreign.csv",
            file_type="csv",
            file_size=self.file.file_size,
            checksum_sha256=hashlib.sha256(self.content).hexdigest(),
        )

        result = backfill_legacy_stored_objects(dry_run=True)

        self.assertEqual(result["would_link_count"], 0)
        self.assertEqual(len(result["errors"]), 1)
        self.assertIsNone(DatasetFile.objects.get(pk=self.file.pk).stored_object_id)
        storage_client_mock.assert_not_called()

    @patch("apps.datasets.services.legacy_backfill.storage_client")
    def test_missing_storage_object_leaves_legacy_row_unlinked(self, storage_client_mock):
        storage_client_mock.return_value = self.storage
        with patch.object(
            self.storage,
            "get_object",
            side_effect=ClientError(
                {"Error": {"Code": "NoSuchKey", "Message": "missing"}},
                "GetObject",
            ),
        ):
            result = backfill_legacy_stored_objects()

        self.assertEqual(result["linked_count"], 0)
        self.assertEqual(len(result["errors"]), 1)
        self.assertIsNone(DatasetFile.objects.get(pk=self.file.pk).stored_object_id)
        self.assertEqual(StoredObject.objects.count(), 0)

    @patch("apps.datasets.services.legacy_backfill.storage_client")
    def test_storage_provider_failure_leaves_legacy_row_unlinked(self, storage_client_mock):
        storage_client_mock.return_value = self.storage
        with patch.object(
            self.storage, "get_object", side_effect=OSError("storage unavailable")
        ):
            result = backfill_legacy_stored_objects()

        self.assertEqual(result["linked_count"], 0)
        self.assertIn("Could not read legacy file", result["errors"][0])
        self.assertIsNone(DatasetFile.objects.get(pk=self.file.pk).stored_object_id)
        self.assertEqual(StoredObject.objects.count(), 0)

    @patch("apps.datasets.services.legacy_backfill.storage_client")
    def test_unique_key_race_is_reported_and_leaves_file_unlinked(
        self, storage_client_mock
    ):
        storage_client_mock.return_value = self.storage
        with patch(
            "apps.datasets.services.legacy_backfill.StoredObject.objects.create",
            side_effect=IntegrityError("unique storage_key"),
        ):
            result = backfill_legacy_stored_objects()

        self.assertEqual(result["linked_count"], 0)
        self.assertIn("database uniqueness or link conflict", result["errors"][0])
        self.assertIsNone(DatasetFile.objects.get(pk=self.file.pk).stored_object_id)
        self.assertEqual(StoredObject.objects.count(), 0)

    @patch("apps.datasets.management.commands.backfill_legacy_stored_objects.backfill_legacy_stored_objects")
    def test_command_reports_scanned_would_link_skipped_and_error_counts(self, backfill_mock):
        backfill_mock.return_value = {
            "scanned_count": 4,
            "linked_count": 0,
            "would_link_count": 2,
            "skipped_count": 1,
            "errors": ["file-id: missing from storage"],
        }
        stdout = StringIO()
        stderr = StringIO()

        with self.assertRaises(CommandError):
            call_command(
                "backfill_legacy_stored_objects",
                dry_run=True,
                stdout=stdout,
                stderr=stderr,
            )

        self.assertIn("Scanned: 4; would link: 2; skipped: 1; errors: 1", stdout.getvalue())
        self.assertIn("file-id: missing from storage", stderr.getvalue())

    @patch("apps.datasets.services.legacy_backfill.storage_client")
    def test_storage_size_mismatch_is_not_linked(self, storage_client_mock):
        storage_client_mock.return_value = self.storage
        DatasetFile.objects.filter(pk=self.file.pk).update(file_size=len(self.content) + 1)

        result = backfill_legacy_stored_objects()

        self.assertEqual(result["linked_count"], 0)
        self.assertIn("bytes in storage", result["errors"][0])
        self.assertIsNone(DatasetFile.objects.get(pk=self.file.pk).stored_object_id)
        self.assertEqual(StoredObject.objects.count(), 0)

    @patch("apps.datasets.services.legacy_backfill.storage_client")
    def test_v2_dataset_file_is_never_backfilled_or_mutated(self, storage_client_mock):
        stored = StoredObject.objects.create(
            dataset=self.dataset,
            storage_key="objects/v2/protected.csv",
            original_filename="protected.csv",
            file_type="csv",
            file_size=10,
            checksum_sha256="a" * 64,
        )
        release = DatasetRelease.objects.create(
            dataset=self.dataset,
            major=1,
            minor=0,
            patch=0,
            metadata_snapshot={"schema_version": 1, "title": self.dataset.title},
        )
        DatasetReleaseFile.objects.create(
            release=release, stored_object=stored, logical_file_id=uuid.uuid4()
        )
        release.status = DatasetRelease.Status.PUBLISHED
        release.save(update_fields=["status"])
        self.dataset.published_release = release
        self.dataset.save(update_fields=["published_release"])

        result = backfill_legacy_stored_objects()

        self.assertEqual(result["linked_count"], 0)
        self.assertEqual(result["v2_skipped_count"], 1)
        self.assertEqual(result["skipped_count"], 1)
        self.assertIsNone(DatasetFile.objects.get(pk=self.file.pk).stored_object_id)
        storage_client_mock.assert_not_called()
