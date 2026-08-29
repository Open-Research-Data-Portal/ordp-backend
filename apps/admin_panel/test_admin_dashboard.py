from datetime import timedelta

from django.utils import timezone
from rest_framework.test import APITestCase
from rest_framework import status

from apps.datasets.factories import make_user
from apps.datasets.models import Dataset, DatasetFile
from apps.accounts.models import ActivityLog
from apps.admin_panel.models import DatasetReviewerAssignment


def make_dataset(owner, title="Test DS", status=Dataset.Status.APPROVED):
    return Dataset.objects.create(title=title, owner=owner, status=status)


class AdminCardsTests(APITestCase):
    def test_cards_reflect_totals(self):
        admin = make_user("cardadmin", "cardadmin@aastu.edu.et", role="admin")
        owner = make_user("cardowner", "cardowner@aastu.edu.et", role="researcher")
        dataset = make_dataset(owner, "Card DS")
        DatasetFile.objects.create(dataset=dataset, file_key="k1", file_type="csv", file_size=1000, checksum="a")
        DatasetFile.objects.create(dataset=dataset, file_key="k2", file_type="csv", file_size=500, checksum="b")
        ActivityLog.objects.create(user=owner, action="dataset_view", target_object=f"Dataset:{dataset.id}", ip_address="127.0.0.1")

        self.client.force_authenticate(admin)
        resp = self.client.get("/api/admin-panel/dashboard/admin/cards/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["total_datasets"], 1)
        self.assertEqual(resp.data["storage_used_bytes"], 1500)
        self.assertEqual(resp.data["recent_activity_count_24h"], 1)

    def test_checker_cannot_access_admin_cards(self):
        checker = make_user("cardchecker", "cardchecker@aastu.edu.et", role="reviewer")
        self.client.force_authenticate(checker)
        resp = self.client.get("/api/admin-panel/dashboard/admin/cards/")
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)


class AdminGraphsTests(APITestCase):
    def test_graphs_zero_fill_days_with_no_activity(self):
        admin = make_user("graphadmin", "graphadmin@aastu.edu.et", role="admin")
        self.client.force_authenticate(admin)
        resp = self.client.get("/api/admin-panel/dashboard/admin/graphs/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(len(resp.data["uploads"]), 31)  # 30 days + today
        self.assertTrue(all(day["count"] == 0 for day in resp.data["uploads"]))

    def test_view_dataset_call_appears_in_views_graph(self):
        admin = make_user("graphadmin2", "graphadmin2@aastu.edu.et", role="admin")
        owner = make_user("graphowner", "graphowner@aastu.edu.et", role="researcher")
        viewer = make_user("graphviewer", "graphviewer@aastu.edu.et", role="researcher")
        dataset = make_dataset(owner, "Graph DS")

        self.client.force_authenticate(viewer)
        self.client.get(f"/api/datasets/{dataset.id}/")

        self.client.force_authenticate(admin)
        resp = self.client.get("/api/admin-panel/dashboard/admin/graphs/")
        today_total = sum(day["count"] for day in resp.data["views"])
        self.assertEqual(today_total, 1)

    def test_old_activity_excluded_from_30_day_window(self):
        admin = make_user("graphadmin3", "graphadmin3@aastu.edu.et", role="admin")
        owner = make_user("graphowner2", "graphowner2@aastu.edu.et", role="researcher")
        dataset = make_dataset(owner, "Old Activity DS")
        old_log = ActivityLog.objects.create(
            user=owner, action="dataset_download", target_object=f"Dataset:{dataset.id}", ip_address="127.0.0.1",
        )
        ActivityLog.objects.filter(id=old_log.id).update(timestamp=timezone.now() - timedelta(days=45))

        self.client.force_authenticate(admin)
        resp = self.client.get("/api/admin-panel/dashboard/admin/graphs/")
        total = sum(day["count"] for day in resp.data["downloads"])
        self.assertEqual(total, 0)



class ReviewerAssignmentRetryTests(APITestCase):
    def test_granting_third_reviewer_assigns_pending_dataset(self):
        admin = make_user(
            "retryadmin",
            "retryadmin@aastu.edu.et",
            role="admin",
        )

        owner = make_user(
            "retryowner",
            "retryowner@aastu.edu.et",
            role="researcher",
        )

        reviewer1 = make_user(
            "retryreviewer1",
            "retryreviewer1@aastu.edu.et",
            role="reviewer",
        )

        reviewer2 = make_user(
            "retryreviewer2",
            "retryreviewer2@aastu.edu.et",
            role="reviewer",
        )

        reviewer3 = make_user(
            "retryreviewer3",
            "retryreviewer3@aastu.edu.et",
            role="public",
        )

        dataset = Dataset.objects.create(
            title="Retry Assignment DS",
            owner=owner,
            status=Dataset.Status.PENDING,
        )

        from apps.datasets.services.assignment import assign_reviewers

        # Only two eligible reviewers exist, so assignment must wait.
        assignments = assign_reviewers(dataset)

        self.assertEqual(assignments, [])
        self.assertEqual(
            DatasetReviewerAssignment.objects.filter(dataset=dataset).count(),
            0,
        )

        self.client.force_authenticate(admin)

        # Grant reviewer role to the third user.
        resp = self.client.post(
            f"/api/admin-panel/users/{reviewer3.id}/grant-role/",
            {"role": "reviewer"},
        )

        self.assertEqual(
            resp.status_code,
            status.HTTP_200_OK,
        )

        self.assertTrue(
            reviewer3.profile.roles.filter(role="reviewer").exists()
        )

        dataset.refresh_from_db()

        self.assertEqual(
            DatasetReviewerAssignment.objects.filter(dataset=dataset).count(),
            3,
        )

        assigned_ids = set(
            DatasetReviewerAssignment.objects.filter(
                dataset=dataset
            ).values_list("reviewer_id", flat=True)
        )

        self.assertEqual(
            assigned_ids,
            {reviewer1.id, reviewer2.id, reviewer3.id},
        )