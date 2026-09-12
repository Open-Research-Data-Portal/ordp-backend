from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from apps.search.services import _dataset_supports_archive_columns


class DatasetArchiveSchemaCompatibilityTests(SimpleTestCase):
    @patch("apps.search.services.connection.introspection.get_table_description")
    def test_dataset_supports_archive_columns_detected_when_present(
        self,
        mock_get_table_description,
    ):
        mock_get_table_description.return_value = [
            SimpleNamespace(name="id"),
            SimpleNamespace(name="is_archived"),
        ]

        self.assertTrue(_dataset_supports_archive_columns())

    @patch("apps.search.services.connection.introspection.get_table_description")
    def test_dataset_supports_archive_columns_false_when_missing(
        self,
        mock_get_table_description,
    ):
        mock_get_table_description.return_value = [
            SimpleNamespace(name="id"),
            SimpleNamespace(name="title"),
        ]

        self.assertFalse(_dataset_supports_archive_columns())
