from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase
from rest_framework import status
from apps.accounts.models import UserProfile
from apps.datasets.models import Dataset
from .models import Category

User = get_user_model()

class AttachMetadataTests(APITestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username="owner1", email="owner1@aastu.edu.et", password="pw12345!")
        profile = self.owner.profile
        profile.full_name = "Owner One"
        profile.save()
        self.other = User.objects.create_user(username="other1", email="other1@aastu.edu.et", password="pw12345!")
        profile = self.other.profile
        profile.full_name = "Other One"
        profile.save()
        self.dataset = Dataset.objects.create(title="Test DS", owner=self.owner)
        self.category = Category.objects.create(name="Health", status=Category.Status.APPROVED)


    def test_owner_can_attach_metadata(self):
        self.client.force_authenticate(self.owner)
        resp = self.client.post(f"/api/metadata/{self.dataset.id}/attach/", {
            "description": "A dataset about health outcomes.",
            "category_id": str(self.category.id),
            "sponsor_or_grant": "NIH Grant #123",
        })
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.dataset.refresh_from_db()
        self.assertEqual(self.dataset.metadata.description, "A dataset about health outcomes.")

    def test_non_owner_cannot_attach_metadata(self):
        self.client.force_authenticate(self.other)
        resp = self.client.post(f"/api/metadata/{self.dataset.id}/attach/", {
            "description": "x", "category_id": str(self.category.id), 
        })
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)