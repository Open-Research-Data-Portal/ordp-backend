import io
import json
import hashlib

from rest_framework.test import APITestCase
from rest_framework import status

from apps.datasets.factories import make_user
from .models import Dataset, DatasetFile


PNG_HEADER = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100  # enough for imghdr.what() to recognize as PNG
VALID_CSV = b"name,age,city\nAlice,30,Addis Ababa\nBob,25,Jimma\n"
VALID_JSON = json.dumps({"a": 1, "b": [1, 2, 3]}).encode()



def upload_and_complete(
    client,
    session_id,
    dataset_id,
    content,
    filename,
    file_type,
    extra=None,
):
    checksum = hashlib.sha256(content).hexdigest()

    # Step 1: Prepare the upload
    prepare_resp = client.post(
        f"/api/datasets/upload/prepare/{session_id}/",
        {
            "filename": filename,
            "file_size": len(content),
            "file_checksum": checksum,
        },
        format="json",
    )

    assert prepare_resp.status_code == status.HTTP_200_OK, prepare_resp.data

    # Step 2: Upload the chunk
    chunk = io.BytesIO(content)
    chunk.name = "chunk_0.bin"

    chunk_resp = client.post(
        f"/api/datasets/upload/chunk/{session_id}/",
        {
            "chunk_index": 0,
            "chunk_checksum": checksum,
            "chunk": chunk,
        },
        format="multipart",
    )

    assert chunk_resp.status_code == status.HTTP_200_OK, chunk_resp.data

    # Step 3: Complete the upload
    payload = {
        "dataset_id": dataset_id,
        "filename": filename,
        "file_type": file_type,
    }

    if extra:
        payload.update(extra)

    return client.post(
        f"/api/datasets/upload/complete/{session_id}/",
        payload,
        format="json",
    )




class FileTypeMismatchTests(APITestCase):
    def setUp(self):
        self.user = make_user("fvuser", "fvuser@aastu.edu.et", role="researcher")
        self.client.force_authenticate(self.user)
        init_resp = self.client.post("/api/datasets/upload/init/", {"title": "FV Dataset"})
        self.dataset_id = init_resp.data["dataset_id"]
        self.session_id = init_resp.data["upload_session_id"]

    def test_image_declared_as_csv_is_rejected(self):
        resp = upload_and_complete(self.client, self.session_id, self.dataset_id,
                                    PNG_HEADER, "sneaky.csv", "csv")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(DatasetFile.objects.filter(dataset_id=self.dataset_id).exists())

    def test_csv_declared_as_png_is_rejected(self):
        resp = upload_and_complete(self.client, self.session_id, self.dataset_id,
                                    VALID_CSV, "sneaky.png", "png")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(DatasetFile.objects.filter(dataset_id=self.dataset_id).exists())

    def test_malformed_json_declared_as_json_is_rejected(self):
        resp = upload_and_complete(self.client, self.session_id, self.dataset_id,
                                    b"{not valid json,,,", "bad.json", "json")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_valid_csv_declared_as_csv_succeeds(self):
        resp = upload_and_complete(self.client, self.session_id, self.dataset_id,
                                    VALID_CSV, "real.csv", "csv")
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertTrue(DatasetFile.objects.filter(id=resp.data["file_id"]).exists())

    def test_valid_json_declared_as_json_succeeds(self):
        resp = upload_and_complete(self.client, self.session_id, self.dataset_id,
                                    VALID_JSON, "real.json", "json")
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)

    def test_valid_png_declared_as_png_succeeds(self):
        resp = upload_and_complete(self.client, self.session_id, self.dataset_id,
                                    PNG_HEADER, "real.png", "png")
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)

    def test_unsupported_declared_type_is_rejected(self):
        """pdf isn't in the supported-format list — hard reject, not silent pass-through."""
        resp = upload_and_complete(self.client, self.session_id, self.dataset_id,
                                    b"%PDF-1.4 some content", "doc.pdf", "pdf")
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)


class StructuredMetadataTests(APITestCase):
    def setUp(self):
        self.user = make_user("smuser", "smuser@aastu.edu.et", role="researcher")
        self.client.force_authenticate(self.user)
        init_resp = self.client.post("/api/datasets/upload/init/", {"title": "SM Dataset"})
        self.dataset_id = init_resp.data["dataset_id"]
        self.session_id = init_resp.data["upload_session_id"]

    def test_structured_file_stores_column_count_and_features(self):
        resp = upload_and_complete(
            self.client, self.session_id, self.dataset_id, VALID_CSV, "structured.csv", "csv",
            extra={"is_structured": True, "column_count": 3, "feature_names": ["name", "age", "city"]},
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        dataset_file = DatasetFile.objects.get(id=resp.data["file_id"])
        self.assertTrue(dataset_file.is_structured)
        self.assertEqual(dataset_file.column_count, 3)
        self.assertEqual(dataset_file.feature_names, ["name", "age", "city"])

    def test_unstructured_file_stores_item_count(self):
        resp = upload_and_complete(
            self.client, self.session_id, self.dataset_id, PNG_HEADER, "images.png", "png",
            extra={"is_structured": False, "item_count": 500},
        )
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        dataset_file = DatasetFile.objects.get(id=resp.data["file_id"])
        self.assertFalse(dataset_file.is_structured)
        self.assertEqual(dataset_file.item_count, 500)

    def test_original_filename_is_stored(self):
        resp = upload_and_complete(self.client, self.session_id, self.dataset_id,
                                    VALID_CSV, "my_survey_data.csv", "csv")
        dataset_file = DatasetFile.objects.get(id=resp.data["file_id"])
        self.assertEqual(dataset_file.original_filename, "my_survey_data.csv")