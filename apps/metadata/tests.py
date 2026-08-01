from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase
from rest_framework import status
from apps.accounts.models import UserProfile
from apps.datasets.models import Dataset
from .models import Category, Subject

User = get_user_model()


class AttachMetadataTests(APITestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username="owner1", email="owner1@aastu.edu.et", password="pw12345!")
        UserProfile.objects.create(user=self.owner, full_name="Owner One")
        self.other = User.objects.create_user(username="other1", email="other1@aastu.edu.et", password="pw12345!")
        UserProfile.objects.create(user=self.other, full_name="Other One")
        self.dataset = Dataset.objects.create(title="Test DS", owner=self.owner)
        self.category = Category.objects.create(name="Health")
        self.subject = Subject.objects.create(name="Public Health")

    def test_owner_can_attach_metadata(self):
        self.client.force_authenticate(self.owner)
        resp = self.client.post(f"/api/metadata/{self.dataset.id}/attach/", {
            "description": "A dataset about health outcomes.",
            "category": str(self.category.id),
            "subject": str(self.subject.id),
            "sponsor_or_grant": "NIH Grant #123",
        })
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.dataset.refresh_from_db()
        self.assertEqual(self.dataset.metadata.description, "A dataset about health outcomes.")

    def test_non_owner_cannot_attach_metadata(self):
        self.client.force_authenticate(self.other)
        resp = self.client.post(f"/api/metadata/{self.dataset.id}/attach/", {
            "description": "x", "category": str(self.category.id), "subject": str(self.subject.id),
        })
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)  # owner=self.owner filter excludes them