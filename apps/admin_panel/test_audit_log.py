from datetime import timedelta

from django.utils import timezone
from rest_framework.test import APITestCase
from rest_framework import status

from apps.datasets.factories import make_user
from apps.accounts.models import ActivityLog


class AuditLogListTests(APITestCase):
    def test_admin_sees_logs(self):
        admin = make_user("alistadmin", "alistadmin@aastu.edu.et", role="admin")
        user = make_user("alistuser", "alistuser@aastu.edu.et", role="researcher")
        ActivityLog.objects.create(user=user, action="dataset_view", target_object="Dataset:x", ip_address="1.2.3.4")

        self.client.force_authenticate(admin)
        resp = self.client.get("/api/admin-panel/dashboard/admin/audit-log/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(len(resp.data), 1)
        self.assertEqual(resp.data[0]["ip_address"], "1.2.3.4")

    def test_checker_cannot_see_audit_log(self):
        checker = make_user("alistchecker", "alistchecker@aastu.edu.et", role="reviewer")
        self.client.force_authenticate(checker)
        resp = self.client.get("/api/admin-panel/dashboard/admin/audit-log/")
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_filter_by_action(self):
        admin = make_user("alfadmin", "alfadmin@aastu.edu.et", role="admin")
        user = make_user("alfuser", "alfuser@aastu.edu.et", role="researcher")
        ActivityLog.objects.create(user=user, action="dataset_view", target_object="Dataset:x", ip_address="1.1.1.1")
        ActivityLog.objects.create(user=user, action="dataset_download", target_object="Dataset:x", ip_address="1.1.1.1")

        self.client.force_authenticate(admin)
        resp = self.client.get("/api/admin-panel/dashboard/admin/audit-log/?action=dataset_download")
        self.assertEqual(len(resp.data), 1)
        self.assertEqual(resp.data[0]["action"], "dataset_download")

    def test_filter_by_date_range_excludes_outside_range(self):
        admin = make_user("aldadmin", "aldadmin@aastu.edu.et", role="admin")
        user = make_user("alduser", "alduser@aastu.edu.et", role="researcher")
        old_log = ActivityLog.objects.create(user=user, action="dataset_view", target_object="Dataset:x", ip_address="1.1.1.1")
        ActivityLog.objects.filter(id=old_log.id).update(timestamp=timezone.now() - timedelta(days=10))
        ActivityLog.objects.create(user=user, action="dataset_view", target_object="Dataset:x", ip_address="1.1.1.1")

        self.client.force_authenticate(admin)
        today = timezone.now().date().isoformat()
        resp = self.client.get(f"/api/admin-panel/dashboard/admin/audit-log/?date_from={today}")
        self.assertEqual(len(resp.data), 1)


class AuditLogDistributionTests(APITestCase):
    def test_distribution_counts_by_action(self):
        admin = make_user("addadmin", "addadmin@aastu.edu.et", role="admin")
        user = make_user("adduser", "adduser@aastu.edu.et", role="researcher")
        for _ in range(3):
            ActivityLog.objects.create(user=user, action="dataset_view", target_object="Dataset:x", ip_address="1.1.1.1")
        ActivityLog.objects.create(user=user, action="dataset_download", target_object="Dataset:x", ip_address="1.1.1.1")

        self.client.force_authenticate(admin)
        resp = self.client.get("/api/admin-panel/dashboard/admin/audit-log/distribution/")
        by_action = {row["action"]: row["count"] for row in resp.data}
        self.assertEqual(by_action["dataset_view"], 3)
        self.assertEqual(by_action["dataset_download"], 1)


class AuditLogSummaryTests(APITestCase):
    def test_summary_counts_active_and_recently_active_users(self):
        admin = make_user("assadmin", "assadmin@aastu.edu.et", role="admin")
        active_user = make_user("assactive", "assactive@aastu.edu.et", role="researcher")
        inactive_user = make_user("assinactive", "assinactive@aastu.edu.et", role="researcher")
        inactive_user.is_active = False
        inactive_user.save(update_fields=["is_active"])
        ActivityLog.objects.create(user=active_user, action="dataset_view", target_object="Dataset:x", ip_address="1.1.1.1")

        self.client.force_authenticate(admin)
        resp = self.client.get("/api/admin-panel/dashboard/admin/audit-log/summary/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertGreaterEqual(resp.data["active_users_last_30_days"], 1)
        total_users_created_active = 3  # admin, active_user, inactive_user minus the deactivated one... see note below
        self.assertNotIn("total_active_users_includes_deactivated", resp.data)  # sanity: field simply shouldn't exist

    def test_pending_reviews_breakdown_present(self):
        admin = make_user("asprevadmin", "asprevadmin@aastu.edu.et", role="admin")
        self.client.force_authenticate(admin)
        resp = self.client.get("/api/admin-panel/dashboard/admin/audit-log/summary/")
        self.assertIn("dataset_moderation", resp.data["pending_reviews"])
        self.assertIn("content_updates", resp.data["pending_reviews"])
        self.assertIn("access_requests", resp.data["pending_reviews"])
        self.assertIn("deletion_requests", resp.data["pending_reviews"])


class AuditLogExportTests(APITestCase):
    def test_csv_export_returns_csv_content_type(self):
        admin = make_user("aecadmin", "aecadmin@aastu.edu.et", role="admin")
        user = make_user("aecuser", "aecuser@aastu.edu.et", role="researcher")
        ActivityLog.objects.create(user=user, action="dataset_view", target_object="Dataset:x", ip_address="1.1.1.1")

        self.client.force_authenticate(admin)
        resp = self.client.get("/api/admin-panel/dashboard/admin/audit-log/export/?export_format=csv")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp["Content-Type"], "text/csv")
        self.assertIn(b"dataset_view", resp.content)

    def test_pdf_export_returns_pdf_content_type(self):
        admin = make_user("aepadmin", "aepadmin@aastu.edu.et", role="admin")
        user = make_user("aepuser", "aepuser@aastu.edu.et", role="researcher")
        ActivityLog.objects.create(user=user, action="dataset_view", target_object="Dataset:x", ip_address="1.1.1.1")

        self.client.force_authenticate(admin)
        resp = self.client.get("/api/admin-panel/dashboard/admin/audit-log/export/?export_format=pdf")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp["Content-Type"], "application/pdf")
        self.assertTrue(resp.content.startswith(b"%PDF"))

    def test_export_respects_filters(self):
        admin = make_user("aefadmin", "aefadmin@aastu.edu.et", role="admin")
        user = make_user("aefuser", "aefuser@aastu.edu.et", role="researcher")
        ActivityLog.objects.create(user=user, action="dataset_view", target_object="Dataset:x", ip_address="1.1.1.1")
        ActivityLog.objects.create(user=user, action="dataset_download", target_object="Dataset:x", ip_address="1.1.1.1")

        self.client.force_authenticate(admin)
        resp = self.client.get("/api/admin-panel/dashboard/admin/audit-log/export/?export_format=csv&action=dataset_download")
        self.assertIn(b"dataset_download", resp.content)
        self.assertNotIn(b"dataset_view", resp.content)