from rest_framework.test import APITestCase
from rest_framework import status

from apps.datasets.factories import make_user
from apps.datasets.models import Dataset
from .models import Language


class SetDatasetLanguagesTests(APITestCase):
    def _dataset_with_metadata(self, owner, title):
        from apps.metadata.models import Category, Metadata

        dataset = Dataset.objects.create(title=title, owner=owner)
        category = Category.objects.create(name=f"{title} Cat", status=Category.Status.APPROVED)
        Metadata.objects.create(dataset=dataset, description="test", category=category)
        return dataset

    def test_approved_language_can_be_set(self):
        researcher = make_user("lresearcher", "lresearcher@aastu.edu.et", role="researcher")
        language = Language.objects.create(name="Amharic", status=Language.Status.APPROVED)
        dataset = self._dataset_with_metadata(researcher, "Lang DS")

        self.client.force_authenticate(researcher)
        resp = self.client.post(f"/api/metadata/{dataset.id}/languages/", {"language_ids": [str(language.id)]})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertIn(language, dataset.metadata.languages.all())

    def test_other_language_created_as_approved_and_usable(self):
        researcher = make_user("olresearcher", "olresearcher@aastu.edu.et", role="researcher")
        dataset = self._dataset_with_metadata(researcher, "Other Lang DS")

        self.client.force_authenticate(researcher)
        resp = self.client.post(f"/api/metadata/{dataset.id}/languages/", {"other_languages": ["Klingon"]})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        language = Language.objects.get(name="Klingon")
        self.assertEqual(language.status, Language.Status.APPROVED)
        self.assertIn(language, dataset.metadata.languages.all())

    def test_no_languages_provided_rejected(self):
        researcher = make_user("nlresearcher", "nlresearcher@aastu.edu.et", role="researcher")
        dataset = Dataset.objects.create(title="No Lang DS", owner=researcher)

        self.client.force_authenticate(researcher)
        resp = self.client.post(f"/api/metadata/{dataset.id}/languages/", {})
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_pending_language_hidden_from_dropdown(self):
        researcher = make_user("plresearcher", "plresearcher@aastu.edu.et", role="researcher")
        Language.objects.create(name="Approved Lang", status=Language.Status.APPROVED)
        Language.objects.create(name="Pending Lang", status=Language.Status.PENDING)

        self.client.force_authenticate(researcher)
        resp = self.client.get("/api/metadata/languages/")
        names = {l["name"] for l in resp.data}
        self.assertIn("Approved Lang", names)
        self.assertNotIn("Pending Lang", names)



class SubmitRequiresLanguageTests(APITestCase):
    def test_submit_blocked_without_language(self):
        """accept_terms_and_submit should reject if no language has been set,
        even if metadata is otherwise complete."""
        from apps.metadata.models import Category, Metadata

        researcher = make_user("slresearcher", "slresearcher@aastu.edu.et", role="researcher")
        dataset = Dataset.objects.create(title="Submit Lang DS", owner=researcher)
        category = Category.objects.create(name="Submit Test Cat", status=Category.Status.APPROVED)
        Metadata.objects.create(dataset=dataset, description="test", category=category)

        self.client.force_authenticate(researcher)
        resp = self.client.post(f"/api/datasets/{dataset.id}/submit/", {"terms_accepted": True})
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)