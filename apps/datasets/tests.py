import io
from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase
from rest_framework import status

from apps.accounts.models import UserProfile, Department, College
from .models import Dataset
from apps.accounts.models import UserProfile, Department
User = get_user_model()


def make_researcher(username, email, completed=True):
    user = User.objects.create_user(username=username, email=email, password="pw12345!")
    department = None
    if completed:
        college, _ = College.objects.get_or_create(name="Test College")
        department, _ = Department.objects.get_or_create(name="Computer Science", college=college)
    profile = UserProfile.objects.create(
        user=user, full_name=username.title(), role="researcher",
        academia="researcher" if completed else "",
        department=department,
        terms_accepted=completed,
    )
    return user, profile


class InitUploadTests(APITestCase):
    def test_incomplete_profile_is_blocked(self):
        user, _ = make_researcher("incompleteuser", "incomplete@aastu.edu.et", completed=False)
        self.client.force_authenticate(user)
        resp = self.client.post("/api/datasets/upload/init/", {"title": "Test Dataset"})
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_public_role_cannot_upload(self):
        user = User.objects.create_user(username="pubuser", email="pub@aastu.edu.et", password="pw12345!")
        college, _ = College.objects.get_or_create(name="Test College")
        department, _ = Department.objects.get_or_create(name="Computer Science", college=college)
        UserProfile.objects.create(
            user=user, full_name="Pub User", role="public",
            academia="student", department=department, terms_accepted=True,
        )
        self.client.force_authenticate(user)
        resp = self.client.post("/api/datasets/upload/init/", {"title": "Test Dataset"})
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_complete_researcher_can_init_upload(self):
        user, _ = make_researcher("completeuser", "complete@aastu.edu.et")
        self.client.force_authenticate(user)
        resp = self.client.post("/api/datasets/upload/init/", {"title": "Test Dataset", "visibility": "restricted"})
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertTrue(Dataset.objects.filter(id=resp.data["dataset_id"], status="draft").exists())

    def test_unauthenticated_user_is_rejected(self):
        resp = self.client.post("/api/datasets/upload/init/", {"title": "Test Dataset"})
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)


class ChunkUploadSizeLimitTests(APITestCase):
    def setUp(self):
        self.user, _ = make_researcher("uploader1", "uploader1@aastu.edu.et")
        self.client.force_authenticate(self.user)
        init_resp = self.client.post("/api/datasets/upload/init/", {"title": "Big Dataset"})
        self.dataset_id = init_resp.data["dataset_id"]
        self.session_id = init_resp.data["upload_session_id"]

    def test_chunk_exceeding_limit_is_rejected(self):
        from django.test import override_settings
        with override_settings(MAX_DATASET_UPLOAD_SIZE=10):  # 10 bytes, deliberately tiny
            chunk = io.BytesIO(b"x" * 100)
            chunk.name = "chunk_0.bin"
            resp = self.client.post(
                f"/api/datasets/upload/chunk/{self.session_id}/",
                {"chunk_index": 0, "chunk": chunk}, format="multipart",
            )
            self.assertEqual(resp.status_code, status.HTTP_413_REQUEST_ENTITY_TOO_LARGE)


class SubmitDatasetTests(APITestCase):
    def setUp(self):
        self.owner, _ = make_researcher("subowner", "subowner@aastu.edu.et")
        self.dataset = Dataset.objects.create(title="Needs Metadata", owner=self.owner)

    def test_submit_without_metadata_is_blocked(self):
        self.client.force_authenticate(self.owner)
        resp = self.client.post(f"/api/datasets/{self.dataset.id}/submit/", {"terms_accepted": True})
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_submit_without_accepting_terms_is_blocked(self):
        from apps.metadata.models import Category, Subject, Metadata
        category = Category.objects.create(name="Cat")
        subject = Subject.objects.create(name="Subj")
        Metadata.objects.create(dataset=self.dataset, description="d", category=category, subject=subject)

        self.client.force_authenticate(self.owner)
        resp = self.client.post(f"/api/datasets/{self.dataset.id}/submit/", {"terms_accepted": False})
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.dataset.refresh_from_db()
        self.assertEqual(self.dataset.status, Dataset.Status.DRAFT)


# class DatasetVisibilityTests(APITestCase):
#     def test_owner_can_soft_delete_own_dataset(self):
#         owner, _ = make_researcher("delowner", "delowner@aastu.edu.et")
#         dataset = Dataset.objects.create(title="To Delete", owner=owner)
#         self.client.force_authenticate(owner)
#         resp = self.client.delete(f"/api/datasets/{dataset.id}/delete/")
#         self.assertEqual(resp.status_code, status.HTTP_204_NO_CONTENT)
#         dataset.refresh_from_db()
#         self.assertFalse(dataset.is_active)

#     def test_non_owner_cannot_soft_delete(self):
#         owner, _ = make_researcher("realowner", "realowner@aastu.edu.et")
#         other, _ = make_researcher("notowner", "notowner@aastu.edu.et")
#         dataset = Dataset.objects.create(title="Protected", owner=owner)
#         self.client.force_authenticate(other)
#         resp = self.client.delete(f"/api/datasets/{dataset.id}/delete/")
#         self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)