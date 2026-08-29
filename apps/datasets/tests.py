import io

from django.contrib.auth import get_user_model
from django.test import override_settings
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
        password="pw12345!",
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
    profile.affiliation = "AASTU"
    profile.academia = UserProfile.Academia.RESEARCHER
    profile.department = department
    profile.profile_visibility = "public"
    profile.terms_accepted = True
    profile.can_upload_datasets = True
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

    def test_complete_user_can_init_upload(self):
        user, _ = make_researcher(
            "completeuser",
            "complete@aastu.edu.et",
        )

        self.client.force_authenticate(user)

        resp = self.client.post(
            "/api/datasets/upload/init/",
            {
                "title": "Test Dataset",
                "visibility": "restricted",
            },
        )

        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertTrue(
            Dataset.objects.filter(
                id=resp.data["dataset_id"],
                status=Dataset.Status.DRAFT,
            ).exists()
        )

    def test_unauthenticated_user_is_rejected(self):
        resp = self.client.post(
            "/api/datasets/upload/init/",
            {"title": "Test Dataset"},
        )

        self.assertEqual(
            resp.status_code,
            status.HTTP_401_UNAUTHORIZED,
        )


class ChunkUploadSizeLimitTests(APITestCase):
    def setUp(self):
        self.user, _ = make_researcher(
            "uploader1",
            "uploader1@aastu.edu.et",
        )

        self.client.force_authenticate(self.user)

        init_resp = self.client.post(
            "/api/datasets/upload/init/",
            {"title": "Big Dataset"},
        )

        self.assertEqual(
            init_resp.status_code,
            status.HTTP_201_CREATED,
            init_resp.data,
        )

        self.dataset_id = init_resp.data["dataset_id"]
        self.session_id = init_resp.data["upload_session_id"]


    def test_chunk_exceeding_limit_is_rejected(self):
        import hashlib

        content = b"x" * 100
        checksum = hashlib.sha256(content).hexdigest()

        # Prepare while the normal upload limit is active.
        prepare_resp = self.client.post(
            f"/api/datasets/upload/prepare/{self.session_id}/",
            {
                "filename": "large.bin",
                "file_size": len(content),
                "file_checksum": checksum,
            },
            format="json",
        )

        self.assertEqual(
            prepare_resp.status_code,
            status.HTTP_200_OK,
            prepare_resp.data,
        )

        # Now lower the limit so the chunk itself exceeds it.
        with override_settings(MAX_DATASET_UPLOAD_SIZE=10):
            chunk = io.BytesIO(content)
            chunk.name = "chunk_0.bin"

            resp = self.client.post(
                f"/api/datasets/upload/chunk/{self.session_id}/",
                {
                    "chunk_index": 0,
                    "chunk_checksum": checksum,
                    "chunk": chunk,
                },
                format="multipart",
            )

            self.assertEqual(
                resp.status_code,
                status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                resp.data,
            )




class SubmitDatasetTests(APITestCase):
    def setUp(self):
        self.owner, _ = make_researcher(
            "subowner",
            "subowner@aastu.edu.et",
        )

        self.dataset = Dataset.objects.create(
            title="Needs Metadata",
            owner=self.owner,
        )

    def test_submit_without_metadata_is_blocked(self):
        self.client.force_authenticate(self.owner)

        resp = self.client.post(
            f"/api/datasets/{self.dataset.id}/submit/",
            {"terms_accepted": True},
        )

        self.assertEqual(
            resp.status_code,
            status.HTTP_400_BAD_REQUEST,
        )

    def test_submit_without_accepting_terms_is_blocked(self):
        from apps.metadata.models import Category, Metadata

        category = Category.objects.create(name="Cat")


        Metadata.objects.create(
            dataset=self.dataset,
            description="d",
            category=category,

        )

        self.client.force_authenticate(self.owner)

        resp = self.client.post(
            f"/api/datasets/{self.dataset.id}/submit/",
            {"terms_accepted": False},
        )

        self.assertEqual(
            resp.status_code,
            status.HTTP_400_BAD_REQUEST,
        )

        self.dataset.refresh_from_db()

        self.assertEqual(
            self.dataset.status,
            Dataset.Status.DRAFT,
        )

