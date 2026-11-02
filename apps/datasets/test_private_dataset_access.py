from unittest.mock import patch

from django.test import override_settings
from rest_framework import status
from rest_framework.test import APITestCase

from apps.accounts.models import ActivityLog
from apps.admin_panel.models import DatasetReviewerAssignment
from apps.datasets.factories import make_user
from apps.datasets.models import Dataset, DatasetFile
from apps.metadata.models import Category, Language, Metadata


def make_private_dataset(owner):
    dataset = Dataset.objects.create(
        title="Private review dataset",
        owner=owner,
        visibility=Dataset.Visibility.PRIVATE,
        status=Dataset.Status.PENDING,
        thumbnail_key="thumbnails/private-review-dataset.png",
    )
    DatasetFile.objects.create(
        dataset=dataset,
        file_key="datasets/private-review-dataset.csv",
        original_filename="private-review-dataset.csv",
        file_type="csv",
        file_size=100,
        checksum="checksum",
    )
    return dataset


class PrivateDatasetReviewAccessTests(APITestCase):
    def test_assigned_reviewer_can_access_details_previews_and_file(self):
        owner = make_user("privateassignedowner", "privateassignedowner@example.com")
        reviewer = make_user(
            "privateassignedreviewer",
            "privateassignedreviewer@example.com",
            role="reviewer",
        )
        dataset = make_private_dataset(owner)
        DatasetReviewerAssignment.objects.create(dataset=dataset, reviewer=reviewer)
        self.client.force_authenticate(reviewer)

        with patch(
            "apps.datasets.serializers.preview_tabular_file",
            return_value={"available": True, "rows": [["private row"]]},
        ), patch(
            "apps.datasets.serializers.presigned_download_url",
            return_value="https://storage.example/thumbnail",
        ):
            detail = self.client.get(f"/api/datasets/{dataset.id}/")

        self.assertEqual(detail.status_code, status.HTTP_200_OK)
        self.assertEqual(detail.data["files"][0]["preview_rows"]["rows"], [["private row"]])
        self.assertEqual(detail.data["data_preview"]["rows"], [["private row"]])
        self.assertEqual(detail.data["thumbnail_url"], "https://storage.example/thumbnail")

        with patch(
            "apps.datasets.views.presigned_download_url",
            return_value="https://storage.example/thumbnail",
        ):
            thumbnail = self.client.get(f"/api/datasets/{dataset.id}/thumbnail/url/")
        self.assertEqual(thumbnail.status_code, status.HTTP_200_OK)

        with patch(
            "apps.sharing.views.presigned_download_url",
            return_value="https://storage.example/dataset",
        ):
            download = self.client.get(f"/api/sharing/{dataset.id}/download/")
        self.assertEqual(download.status_code, status.HTTP_200_OK)
        self.assertEqual(download.data["download_url"], "https://storage.example/dataset")

    def test_stale_assigned_reviewer_reference_does_not_grant_private_access(self):
        owner = make_user("privatestaleowner", "privatestaleowner@example.com")
        reviewer = make_user(
            "privatestale",
            "privatestale@example.com",
            role="reviewer",
        )
        dataset = make_private_dataset(owner)
        dataset.assigned_reviewer = reviewer
        dataset.save(update_fields=["assigned_reviewer"])
        self.client.force_authenticate(reviewer)

        queue = self.client.get("/api/admin-panel/queue/")
        detail = self.client.get(f"/api/datasets/{dataset.id}/")
        decision = self.client.post(
            f"/api/admin-panel/{dataset.id}/decide/",
            {"decision": "approved"},
        )

        self.assertNotIn(str(dataset.id), {item["id"] for item in queue.data})
        self.assertEqual(detail.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(decision.status_code, status.HTTP_403_FORBIDDEN)

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_private_submission_assigns_reviewers_and_supports_the_full_review_flow(self):
        owner = make_user("privatesubmitowner", "privatesubmitowner@example.com")
        reviewers = [
            make_user(
                f"privatesubmitreviewer{i}",
                f"privatesubmitreviewer{i}@example.com",
                role="reviewer",
            )
            for i in range(2)
        ]
        dataset = Dataset.objects.create(
            title="Private submitted dataset",
            owner=owner,
            visibility=Dataset.Visibility.PRIVATE,
            status=Dataset.Status.DRAFT,
        )
        category = Category.objects.create(
            name="Private submission category",
            status=Category.Status.APPROVED,
        )
        metadata = Metadata.objects.create(
            dataset=dataset,
            description="Private dataset pending moderator review",
            category=category,
        )
        language = Language.objects.create(
            name="Private submission language",
            status=Language.Status.APPROVED,
        )
        metadata.languages.add(language)
        DatasetFile.objects.create(
            dataset=dataset,
            file_key="datasets/private-submission.csv",
            original_filename="private-submission.csv",
            file_type="csv",
            file_size=100,
            checksum="submission-checksum",
        )

        self.client.force_authenticate(owner)
        submitted = self.client.post(
            f"/api/datasets/{dataset.id}/submit/",
            {"terms_accepted": True},
        )
        self.assertEqual(submitted.status_code, status.HTTP_200_OK)
        dataset.refresh_from_db()
        self.assertEqual(dataset.status, Dataset.Status.PENDING)

        assigned_ids = set(
            DatasetReviewerAssignment.objects.filter(dataset=dataset)
            .values_list("reviewer_id", flat=True)
        )
        self.assertEqual(assigned_ids, {reviewer.id for reviewer in reviewers})

        unassigned = make_user(
            "privatesubmitunassigned",
            "privatesubmitunassigned@example.com",
            role="reviewer",
        )
        for reviewer in reviewers:
            self.client.force_authenticate(reviewer)
            with patch(
                "apps.datasets.serializers.preview_tabular_file",
                return_value={"available": True, "rows": [["review data"]]},
            ):
                queue = self.client.get("/api/admin-panel/queue/")
                detail = self.client.get(f"/api/datasets/{dataset.id}/")
            self.assertIn(str(dataset.id), {item["id"] for item in queue.data})
            self.assertEqual(detail.status_code, status.HTTP_200_OK)
            self.assertEqual(
                detail.data["data_preview"]["rows"],
                [["review data"]],
            )

        self.client.force_authenticate(unassigned)
        queue = self.client.get("/api/admin-panel/queue/")
        detail = self.client.get(f"/api/datasets/{dataset.id}/")
        self.assertNotIn(str(dataset.id), {item["id"] for item in queue.data})
        self.assertEqual(detail.status_code, status.HTTP_404_NOT_FOUND)

        reviewer = reviewers[0]
        self.client.force_authenticate(reviewer)
        with patch(
            "apps.sharing.views.presigned_download_url",
            return_value="https://storage.example/private-submission.csv",
        ):
            download = self.client.get(f"/api/sharing/{dataset.id}/download/")

        self.assertEqual(download.status_code, status.HTTP_200_OK)
        self.assertEqual(
            ActivityLog.objects.filter(
                user=reviewer,
                action="reviewer_download",
                target_object=f"Dataset:{dataset.id}",
            ).count(),
            1,
        )
        dataset.refresh_from_db()
        self.assertEqual(dataset.download_count, 1)

    def test_unassigned_reviewer_cannot_access_private_dataset_content(self):
        owner = make_user("privateunassignedowner", "privateunassignedowner@example.com")
        reviewer = make_user(
            "privateunassignedreviewer",
            "privateunassignedreviewer@example.com",
            role="reviewer",
        )
        dataset = make_private_dataset(owner)
        self.client.force_authenticate(reviewer)

        detail = self.client.get(f"/api/datasets/{dataset.id}/")
        thumbnail = self.client.get(f"/api/datasets/{dataset.id}/thumbnail/url/")
        versions = self.client.get(f"/api/datasets/{dataset.id}/versions/")
        download = self.client.get(f"/api/sharing/{dataset.id}/download/")

        self.assertEqual(detail.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(thumbnail.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(versions.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(download.status_code, status.HTTP_403_FORBIDDEN)

    def test_administrator_can_access_private_dataset_details_and_file(self):
        owner = make_user("privateadminowner", "privateadminowner@example.com")
        admin = make_user("privateadmin", "privateadmin@example.com", role="admin")
        dataset = make_private_dataset(owner)
        self.client.force_authenticate(admin)

        with patch(
            "apps.datasets.serializers.preview_tabular_file",
            return_value={"available": True, "rows": []},
        ):
            detail = self.client.get(f"/api/datasets/{dataset.id}/")
        with patch(
            "apps.sharing.views.presigned_download_url",
            return_value="https://storage.example/dataset",
        ):
            download = self.client.get(f"/api/sharing/{dataset.id}/download/")

        self.assertEqual(detail.status_code, status.HTTP_200_OK)
        self.assertEqual(download.status_code, status.HTTP_200_OK)

    def test_owner_can_access_private_dataset_details_and_file(self):
        owner = make_user("privateowner", "privateowner@example.com")
        dataset = make_private_dataset(owner)
        self.client.force_authenticate(owner)

        with patch(
            "apps.datasets.serializers.preview_tabular_file",
            return_value={"available": True, "rows": []},
        ):
            detail = self.client.get(f"/api/datasets/{dataset.id}/")
        with patch(
            "apps.sharing.views.presigned_download_url",
            return_value="https://storage.example/dataset",
        ):
            download = self.client.get(f"/api/sharing/{dataset.id}/download/")

        self.assertEqual(detail.status_code, status.HTTP_200_OK)
        self.assertEqual(download.status_code, status.HTTP_200_OK)

    def test_ordinary_researcher_cannot_access_private_dataset_content(self):
        owner = make_user("privateordinaryowner", "privateordinaryowner@example.com")
        researcher = make_user("privateordinary", "privateordinary@example.com")
        dataset = make_private_dataset(owner)
        self.client.force_authenticate(researcher)

        detail = self.client.get(f"/api/datasets/{dataset.id}/")
        download = self.client.get(f"/api/sharing/{dataset.id}/download/")

        self.assertEqual(detail.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(download.status_code, status.HTTP_403_FORBIDDEN)
