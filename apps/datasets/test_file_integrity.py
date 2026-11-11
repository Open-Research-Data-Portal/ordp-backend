import hashlib
import io
from unittest.mock import patch

from botocore.exceptions import ClientError
from django.test import SimpleTestCase
from rest_framework import status
from rest_framework.test import APITestCase

from apps.datasets.factories import make_user
from apps.datasets.models import Dataset, DatasetFile, StoredObject, UploadSession
from apps.datasets.services.file_integrity import safe_filename
from apps.datasets.services.storage import StorageVerificationError, verify_object
from apps.datasets.test_file_validation import upload_and_complete, VALID_CSV

PUSH = "apps.datasets.services.file_integrity.push_to_storage"
VERIFY = "apps.datasets.services.file_integrity.verify_object"

class SafeFilenameTests(SimpleTestCase):
    def test_plain_name_unchanged(self):
        self.assertEqual(safe_filename("survey.csv"), "survey.csv")

    def test_directory_parts_are_stripped(self):
        self.assertEqual(safe_filename("../../etc/passwd"), "passwd")
        self.assertEqual(safe_filename("a/b/c.csv"), "c.csv")

    def test_windows_separators_are_stripped(self):
        self.assertEqual(safe_filename("C:\\Users\\me\\data.csv"), "data.csv")

    def test_empty_name_gets_fallback(self):
        self.assertEqual(safe_filename(""), "file")
        self.assertEqual(safe_filename("   "), "file")
class VerifyObjectTests(SimpleTestCase):
    @patch("apps.datasets.services.storage.storage_client")
    def test_matching_size_passes(self, mock_client):
        mock_client.return_value.head_object.return_value = {"ContentLength": 10}
        self.assertEqual(verify_object("k", 10), {"ContentLength": 10})

    @patch("apps.datasets.services.storage.storage_client")
    def test_wrong_size_raises(self, mock_client):
        mock_client.return_value.head_object.return_value = {"ContentLength": 9}
        with self.assertRaises(StorageVerificationError):
            verify_object("k", 10)

    @patch("apps.datasets.services.storage.storage_client")
    def test_missing_object_raises(self, mock_client):
        mock_client.return_value.head_object.side_effect = ClientError(
            {"Error": {"Code": "404", "Message": "Not Found"}}, "HeadObject"
        )
        with self.assertRaises(StorageVerificationError):
            verify_object("k", 10)
class ImmutableUploadFlowTests(APITestCase):
    def setUp(self):
        self.user = make_user("fiuser", "fiuser@aastu.edu.et", role="researcher")
        self.client.force_authenticate(self.user)
        init = self.client.post("/api/datasets/upload/init/", {"title": "FI Dataset"})
        self.dataset_id = init.data["dataset_id"]
        self.session_id = init.data["upload_session_id"]

    def _new_session(self):
        resp = self.client.post(f"/api/datasets/{self.dataset_id}/upload/init/")
        return resp.data["upload_session_id"]

    @patch(VERIFY, return_value={})
    @patch(PUSH)
    def test_upload_creates_stored_object_and_linked_dataset_file(self, mock_push, mock_verify):
        resp = upload_and_complete(
            self.client, self.session_id, self.dataset_id, VALID_CSV, "survey.csv", "csv",
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)

        stored = StoredObject.objects.get(dataset_id=self.dataset_id)
        legacy = DatasetFile.objects.get(dataset_id=self.dataset_id)

        # Both records reference the same single uploaded object.
        self.assertEqual(legacy.stored_object_id, stored.id)
        self.assertEqual(legacy.file_key, stored.storage_key)
        self.assertEqual(mock_push.call_count, 1)  # uploaded exactly once
        self.assertEqual(mock_push.call_args.args[1], stored.storage_key)

        self.assertEqual(stored.storage_key, f"objects/{self.dataset_id}/{stored.id}/survey.csv")
        self.assertEqual(stored.checksum_sha256, hashlib.sha256(VALID_CSV).hexdigest())
        self.assertEqual(stored.file_size, len(VALID_CSV))
        self.assertEqual(stored.uploaded_by_id, self.user.id)
        self.assertIsNotNone(stored.verified_at)
        mock_verify.assert_called_once_with(
            stored.storage_key, len(VALID_CSV), hashlib.sha256(VALID_CSV).hexdigest(),
        )

    @patch(VERIFY, return_value={})
    @patch(PUSH)
    def test_same_filename_twice_never_reuses_a_storage_key(self, mock_push, mock_verify):
        upload_and_complete(self.client, self.session_id, self.dataset_id, VALID_CSV, "survey.csv", "csv")
        second_session = self._new_session()
        upload_and_complete(self.client, second_session, self.dataset_id, VALID_CSV, "survey.csv", "csv")

        keys = list(StoredObject.objects.filter(dataset_id=self.dataset_id).values_list("storage_key", flat=True))
        self.assertEqual(len(keys), 2)
        self.assertEqual(len(set(keys)), 2)  # distinct keys, nothing overwritten
        pushed_keys = [call.args[1] for call in mock_push.call_args_list]
        self.assertEqual(set(pushed_keys), set(keys))

    @patch(VERIFY, side_effect=StorageVerificationError("missing"))
    @patch(PUSH)
    def test_failed_verification_creates_no_records_and_keeps_session(self, mock_push, mock_verify):
        resp = upload_and_complete(
            self.client, self.session_id, self.dataset_id, VALID_CSV, "survey.csv", "csv",
        )
        self.assertEqual(resp.status_code, status.HTTP_500_INTERNAL_SERVER_ERROR)
        self.assertFalse(StoredObject.objects.filter(dataset_id=self.dataset_id).exists())
        self.assertFalse(DatasetFile.objects.filter(dataset_id=self.dataset_id).exists())
        # Session stays open so the user can retry.
        self.assertIsNone(UploadSession.objects.get(id=self.session_id).completed_at)

    @patch(VERIFY, return_value={})
    @patch(PUSH)
    def test_path_traversal_in_filename_cannot_shape_storage_key(self, mock_push, mock_verify):
        upload_and_complete(
            self.client, self.session_id, self.dataset_id, VALID_CSV, "../../evil.csv", "csv",
        )
        stored = StoredObject.objects.get(dataset_id=self.dataset_id)
        self.assertEqual(stored.storage_key, f"objects/{self.dataset_id}/{stored.id}/evil.csv")


class SHA256ChecksumValidationTests(SimpleTestCase):
    def test_valid_sha256(self):
        from apps.datasets.services.file_integrity import is_valid_sha256, validate_sha256
        valid = "a" * 64
        self.assertTrue(is_valid_sha256(valid))
        self.assertEqual(validate_sha256(valid), valid)

    def test_uppercase_normalized_to_lowercase(self):
        from apps.datasets.services.file_integrity import is_valid_sha256, validate_sha256
        upper = "A" * 64
        self.assertTrue(is_valid_sha256(upper))
        self.assertEqual(validate_sha256(upper), "a" * 64)

    def test_invalid_sha256_rejected(self):
        from apps.datasets.services.file_integrity import is_valid_sha256, validate_sha256
        self.assertFalse(is_valid_sha256("short"))
        self.assertFalse(is_valid_sha256("z" * 64))  # non-hex
        self.assertFalse(is_valid_sha256(None))
        self.assertFalse(is_valid_sha256(12345))

        with self.assertRaises(ValueError):
            validate_sha256("z" * 64)

        with self.assertRaises(ValueError):
            validate_sha256("a" * 63)