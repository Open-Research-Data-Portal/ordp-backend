from io import BytesIO
from tempfile import NamedTemporaryFile
from unittest.mock import MagicMock, patch

from django.conf import settings
from django.test import SimpleTestCase

from apps.datasets.services.storage import (
    download_to_file,
    presigned_download_url,
    push_to_storage,
    storage_client,
    upload_fileobj,
)


class StorageClientTests(SimpleTestCase):

    @patch("apps.datasets.services.storage.Config")
    @patch("apps.datasets.services.storage.boto3.client")
    def test_storage_client_uses_s3_configuration(
        self,
        mock_boto_client,
        mock_config,
    ):
        mock_client = MagicMock()
        mock_boto_client.return_value = mock_client

        result = storage_client()

        mock_config.assert_called_once_with(
            signature_version="s3v4"
        )

        mock_boto_client.assert_called_once_with(
            "s3",
            endpoint_url=settings.OBJECT_STORAGE_ENDPOINT_URL or None,
            aws_access_key_id=settings.OBJECT_STORAGE_ACCESS_KEY,
            aws_secret_access_key=settings.OBJECT_STORAGE_SECRET_KEY,
            region_name=settings.OBJECT_STORAGE_REGION,
            config=mock_config.return_value,
        )

        self.assertIs(result, mock_client)


class PushToStorageTests(SimpleTestCase):

    @patch("apps.datasets.services.storage.storage_client")
    def test_push_to_storage_uploads_file(self, mock_storage_client):
        mock_client = mock_storage_client.return_value

        with NamedTemporaryFile() as temp_file:
            result = push_to_storage(
                temp_file.name,
                "datasets/test.csv",
            )

            mock_client.upload_file.assert_called_once_with(
                temp_file.name,
                settings.OBJECT_STORAGE_BUCKET,
                "datasets/test.csv",
            )

        self.assertEqual(result, "datasets/test.csv")


class UploadFileObjTests(SimpleTestCase):

    @patch("apps.datasets.services.storage.storage_client")
    def test_upload_fileobj_without_content_type(self, mock_storage_client):
        mock_client = mock_storage_client.return_value
        file_obj = BytesIO(b"hello world")

        result = upload_fileobj(
            file_obj,
            "datasets/test.txt",
        )

        mock_client.upload_fileobj.assert_called_once_with(
            file_obj,
            settings.OBJECT_STORAGE_BUCKET,
            "datasets/test.txt",
        )

        self.assertEqual(result, "datasets/test.txt")

    @patch("apps.datasets.services.storage.storage_client")
    def test_upload_fileobj_with_content_type(self, mock_storage_client):
        mock_client = mock_storage_client.return_value
        file_obj = BytesIO(b"hello world")

        result = upload_fileobj(
            file_obj,
            "datasets/test.txt",
            "text/plain",
        )

        mock_client.upload_fileobj.assert_called_once_with(
            file_obj,
            settings.OBJECT_STORAGE_BUCKET,
            "datasets/test.txt",
            ExtraArgs={"ContentType": "text/plain"},
        )

        self.assertEqual(result, "datasets/test.txt")


class PresignedDownloadUrlTests(SimpleTestCase):

    @patch("apps.datasets.services.storage.storage_client")
    def test_presigned_download_url(self, mock_storage_client):
        mock_client = mock_storage_client.return_value

        mock_client.generate_presigned_url.return_value = (
            "https://example.com/presigned-url"
        )

        result = presigned_download_url(
            "datasets/test.csv",
            expires_seconds=1800,
        )

        mock_client.generate_presigned_url.assert_called_once_with(
            "get_object",
            Params={
                "Bucket": settings.OBJECT_STORAGE_BUCKET,
                "Key": "datasets/test.csv",
            },
            ExpiresIn=1800,
        )

        self.assertEqual(
            result,
            "https://example.com/presigned-url",
        )


class DownloadToFileTests(SimpleTestCase):

    @patch("apps.datasets.services.storage.storage_client")
    def test_download_to_file(self, mock_storage_client):
        mock_client = mock_storage_client.return_value

        with NamedTemporaryFile() as temp_file:
            result = download_to_file(
                "datasets/test.csv",
                temp_file.name,
            )

            mock_client.download_file.assert_called_once_with(
                settings.OBJECT_STORAGE_BUCKET,
                "datasets/test.csv",
                temp_file.name,
            )

        self.assertEqual(result, temp_file.name)