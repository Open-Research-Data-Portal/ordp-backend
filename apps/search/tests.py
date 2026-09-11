from rest_framework import status
from rest_framework.test import APITestCase

from apps.datasets.factories import make_user
from apps.datasets.models import Dataset
from apps.datasets.serializers import DatasetSerializer
from apps.metadata.models import Category, Language, Metadata


class DatasetCatalogSerializationTests(APITestCase):
    def test_search_returns_published_dataset_without_metadata(self):
        owner = make_user("catowner", "catowner@aastu.edu.et")
        Dataset.objects.create(
            title="Published Without Metadata",
            owner=owner,
            status=Dataset.Status.PUBLISHED,
            visibility=Dataset.Visibility.PUBLIC,
        )

        resp = self.client.get("/api/search/datasets/")

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        titles = {item["title"] for item in resp.data}
        self.assertIn("Published Without Metadata", titles)

    def test_serializer_handles_missing_metadata_and_remote_thumbnail(self):
        owner = make_user("serowner", "serowner@aastu.edu.et")
        dataset = Dataset.objects.create(
            title="Serializer Guard DS",
            owner=owner,
            status=Dataset.Status.PUBLISHED,
            visibility=Dataset.Visibility.PUBLIC,
            thumbnail_key="https://picsum.photos/seed/fallback-1/600/400",
        )

        data = DatasetSerializer(dataset).data

        self.assertIsNone(data["metadata"])
        self.assertIsNone(data["category"])
        self.assertEqual(data["languages"], [])
        self.assertEqual(data["characteristics"], [])
        self.assertEqual(data["thumbnail_url"], dataset.thumbnail_key)
        self.assertEqual(data["owner_name"], owner.profile.full_name)

    def test_serializer_includes_metadata_when_present(self):
        owner = make_user("metaowner", "metaowner@aastu.edu.et")
        dataset = Dataset.objects.create(
            title="With Metadata DS",
            owner=owner,
            status=Dataset.Status.PUBLISHED,
            visibility=Dataset.Visibility.PUBLIC,
        )
        category = Category.objects.create(name="Climate")
        language = Language.objects.create(name="English")
        metadata = Metadata.objects.create(
            dataset=dataset,
            description="A complete dataset",
            category=category,
        )
        metadata.languages.add(language)

        data = DatasetSerializer(dataset).data

        self.assertEqual(data["category"], "Climate")
        self.assertEqual(data["description"], "A complete dataset")
        self.assertEqual(data["languages"], ["English"])
        self.assertEqual(data["metadata"]["category_name"], "Climate")
