from rest_framework.test import APITestCase
from rest_framework import status
from django.contrib.auth import get_user_model
from apps.datasets.factories import make_user
from apps.datasets.models import Dataset
from apps.admin_panel.models import SystemSetting
from apps.datasets.licensing import (
    DEFAULT_LICENSE_KEY,
    get_system_default_license,
    get_supported_licenses,
    LICENSE_CATALOG,
)

User = get_user_model()


class LicensingApiTests(APITestCase):
    def setUp(self):
        self.user = make_user("researcher_license", "researcher_license@aastu.edu.et")
        self.admin = make_user("admin_license", "admin_license@aastu.edu.et", role="admin")

    def test_get_licensing_options_public(self):
        resp = self.client.get("/api/datasets/licensing/options/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertIn("supported_licenses", resp.data)
        self.assertIn("default_license", resp.data)
        self.assertEqual(len(resp.data["supported_licenses"]), 5)
        self.assertEqual(resp.data["default_license"], "CC-BY-4.0")

    def test_admin_updates_default_license(self):
        self.client.force_authenticate(self.admin)
        resp = self.client.post("/api/admin-panel/settings/licensing/", {
            "default_license": "CC0-1.0",
        })
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["default_license"], "CC0-1.0")
        self.assertEqual(get_system_default_license(), "CC0-1.0")

        # Now public options reflect the updated default
        resp2 = self.client.get("/api/datasets/licensing/options/")
        self.assertEqual(resp2.data["default_license"], "CC0-1.0")

    def test_admin_invalid_license_rejected(self):
        self.client.force_authenticate(self.admin)
        resp = self.client.post("/api/admin-panel/settings/licensing/", {
            "default_license": "UNKNOWN-LICENSE",
        })
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_non_admin_cannot_change_default_license(self):
        self.client.force_authenticate(self.user)
        resp = self.client.post("/api/admin-panel/settings/licensing/", {
            "default_license": "CC0-1.0",
        })
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
