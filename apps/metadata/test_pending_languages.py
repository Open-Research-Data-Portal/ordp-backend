from rest_framework.test import APITestCase
from rest_framework import status

from apps.datasets.factories import make_user
from apps.datasets.models import Dataset
from .models import Language


class SetDatasetLanguagesTests(APITestCase):
    def test_approved_language_can_be_set(self):
        researcher = make_user("lresearcher", "lresearcher@aastu.edu.et", role="researcher")
        language = Language.objects.create(name="Amharic", status=Language.Status.APPROVED)
        dataset = Dataset.objects.create(title="Lang DS", owner=researcher)

        self.client.force_authenticate(researcher)
        resp = self.client.post(f"/api/metadata/{dataset.id}/languages/", {"language_ids": [str(language.id)]})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertIn(language, dataset.languages.all())

    def test_other_language_created_as_pending_and_still_usable(self):
        researcher = make_user("olresearcher", "olresearcher@aastu.edu.et", role="researcher")
        dataset = Dataset.objects.create(title="Other Lang DS", owner=researcher)

        self.client.force_authenticate(researcher)
        resp = self.client.post(f"/api/metadata/{dataset.id}/languages/", {"other_languages": ["Klingon"]})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        language = Language.objects.get(name="Klingon")
        self.assertEqual(language.status, Language.Status.PENDING)
        self.assertIn(language, dataset.languages.all())

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


class AdminLanguageReviewTests(APITestCase):
    def test_admin_can_approve_pending_language(self):
        admin = make_user("laadmin", "laadmin@aastu.edu.et", role="admin")
        language = Language.objects.create(name="Newly Suggested", status=Language.Status.PENDING)

        self.client.force_authenticate(admin)
        resp = self.client.post(f"/api/admin-panel/languages/{language.id}/decide/", {"decision": "approve"})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        language.refresh_from_db()
        self.assertEqual(language.status, Language.Status.APPROVED)

    def test_researcher_cannot_decide_pending_language(self):
        researcher = make_user("lndresearcher", "lndresearcher@aastu.edu.et", role="researcher")
        language = Language.objects.create(name="Off Limits Lang", status=Language.Status.PENDING)

        self.client.force_authenticate(researcher)
        resp = self.client.post(f"/api/admin-panel/languages/{language.id}/decide/", {"decision": "approve"})
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)


class SubmitRequiresLanguageTests(APITestCase):
    def test_submit_blocked_without_language(self):
        """accept_terms_and_submit should reject if no language has been set,
        even if metadata is otherwise complete."""
        from apps.metadata.models import Category, Subject, Metadata

        researcher = make_user("slresearcher", "slresearcher@aastu.edu.et", role="researcher")
        dataset = Dataset.objects.create(title="Submit Lang DS", owner=researcher)
        category = Category.objects.create(name="Submit Test Cat", status=Category.Status.APPROVED)
        subject = Subject.objects.create(name="Submit Test Subj")
        Metadata.objects.create(dataset=dataset, description="test", category=category, subject=subject)

        self.client.force_authenticate(researcher)
        resp = self.client.post(f"/api/datasets/{dataset.id}/submit/", {"terms_accepted": True})
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)