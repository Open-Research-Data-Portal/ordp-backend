from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase
from rest_framework import status
from apps.metadata.models import Category, Subject, Metadata
from apps.accounts.models import UserProfile
from apps.datasets.models import Dataset
from apps.datasets.factories import make_user
from apps.notifications.models import Notification

User = get_user_model()
class ModerationTests(APITestCase):
    def setUp(self):
        self.owner = make_user("modowner", "modowner@aastu.edu.et", "researcher")
        self.checker = make_user("checker1", "checker1@aastu.edu.et", "checker")
        self.researcher = make_user("plainresearcher", "plain@aastu.edu.et", "researcher")
        self.category = Category.objects.create(name="Health")
        self.checker.profile.expertise.add(self.category)
        self.dataset = Dataset.objects.create(
            title="Under Review", owner=self.owner, status=Dataset.Status.PENDING,
            assigned_reviewer=self.checker,
        )
        subject = Subject.objects.create(name="Public Health")
        Metadata.objects.create(dataset=self.dataset, description="d", category=self.category, subject=subject)

    def test_researcher_cannot_access_moderation_queue(self):
        self.client.force_authenticate(self.researcher)
        resp = self.client.get("/api/admin-panel/queue/")
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_checker_sees_pending_dataset_in_queue(self):
        self.client.force_authenticate(self.checker)
        resp = self.client.get("/api/admin-panel/queue/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(len(resp.data), 1)

    def test_reject_requires_reason(self):
        self.client.force_authenticate(self.checker)
        resp = self.client.post(f"/api/admin-panel/{self.dataset.id}/decide/", {"decision": "rejected"})
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_approve_updates_status_and_notifies_owner(self):
        self.client.force_authenticate(self.checker)
        resp = self.client.post(f"/api/admin-panel/{self.dataset.id}/decide/", {"decision": "approved"})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.dataset.refresh_from_db()
        self.assertEqual(self.dataset.status, Dataset.Status.APPROVED)
        self.assertTrue(Notification.objects.filter(
            user=self.owner, notification_type=Notification.NotificationType.DATASET_APPROVED
        ).exists())

    def test_reject_with_reason_notifies_owner(self):
        self.client.force_authenticate(self.checker)
        resp = self.client.post(f"/api/admin-panel/{self.dataset.id}/decide/",
                                 {"decision": "rejected", "reason": "Missing consent documentation."})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        notif = Notification.objects.get(user=self.owner, notification_type=Notification.NotificationType.DATASET_REJECTED)
        self.assertIn("Missing consent documentation.", notif.reason)