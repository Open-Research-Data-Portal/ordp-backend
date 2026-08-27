import io
from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase
from rest_framework import status

from .factories import make_user
from apps.accounts.models import UserProfile, Department, College
from .models import Dataset

User = get_user_model()


def make_researcher(username, email):
    user = User.objects.create_user(
        username=username,
        email=email,
        password="pw12345!"
    )

    college = College.objects.create(
        name=f"{username} College"
    )

    department = Department.objects.create(
        name=f"{username} Department",
        college=college,
    )

    profile = user.profile

    profile.full_name = username.title()
    profile.role = UserProfile.Role.RESEARCHER
    profile.academia = "researcher"
    profile.department = department
    profile.terms_accepted = True
    profile.save()

    return user, profile


class InitUploadTests(APITestCase):
  def test_public_role_cannot_upload(self):
    user = User.objects.create_user(
        username="pubuser",
        email="pub@aastu.edu.et",
        password="pw12345!",
    )

    self.client.force_authenticate(user)

    resp = self.client.post(
        "/api/datasets/upload/init/",
        {"title": "Test Dataset"},
    )

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

class UploadRestrictedToResearcherTests(APITestCase):
    def test_admin_cannot_init_upload(self):
        admin = make_user("uadmin", "uadmin@aastu.edu.et", role="admin")
        self.client.force_authenticate(admin)
        resp = self.client.post("/api/datasets/upload/init/", {"title": "Admin Attempt"})
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_public_role_cannot_init_upload(self):
        public_user = make_user("upublic", "upublic@aastu.edu.et", role="public")
        self.client.force_authenticate(public_user)
        resp = self.client.post("/api/datasets/upload/init/", {"title": "Public Attempt"})
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_public_role_cannot_upload_chunk(self):
        """Regression test for the doubled @permission_classes bug — this endpoint
        was silently accepting any authenticated user regardless of role."""
        researcher = make_user("uresearcher", "uresearcher@aastu.edu.et", role="researcher")
        public_user = make_user("upublic2", "upublic2@aastu.edu.et", role="public")

        self.client.force_authenticate(researcher)
        init_resp = self.client.post("/api/datasets/upload/init/", {"title": "Chunk Test"})
        upload_session_id = init_resp.data["upload_session_id"]

        self.client.force_authenticate(public_user)
        resp = self.client.post(
            f"/api/datasets/upload/chunk/{upload_session_id}/",
            {"chunk_index": 0, "chunk": b"data"},
            format="multipart",
        )
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_admin_researcher_dual_role_can_upload(self):
        """Confirms this is role-based, not admin-exclusion-based — an admin who
        ALSO holds researcher should still be able to upload."""
        from apps.accounts.models import UserRole
        admin_researcher = make_user("uboth", "uboth@aastu.edu.et", role="admin")
        UserRole.objects.get_or_create(profile=admin_researcher.profile, role="researcher")

        self.client.force_authenticate(admin_researcher)
        resp = self.client.post("/api/datasets/upload/init/", {"title": "Dual Role Attempt"})
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
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