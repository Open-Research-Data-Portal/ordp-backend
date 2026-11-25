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

    def test_admin_can_create_and_select_custom_license(self):
        self.client.force_authenticate(self.admin)
        create_resp = self.client.post("/api/admin-panel/settings/licensing/licenses/", {
            "key": "MIT-DATA",
            "name": "MIT Data License",
            "short_name": "MIT Data",
            "description": "Permissive data license.",
            "url": "https://opensource.org/licenses/MIT",
            "is_restricted": False,
        })
        self.assertEqual(create_resp.status_code, status.HTTP_201_CREATED)
        self.assertEqual(create_resp.data["license"]["key"], "MIT-DATA")
        self.assertTrue(any(item["key"] == "MIT-DATA" for item in create_resp.data["supported_licenses"]))

        # Admin can set the new custom license as default
        default_resp = self.client.post("/api/admin-panel/settings/licensing/", {
            "default_license": "MIT-DATA",
        })
        self.assertEqual(default_resp.status_code, status.HTTP_200_OK)
        self.assertEqual(default_resp.data["default_license"], "MIT-DATA")

        # Public options endpoint includes the custom license
        opts_resp = self.client.get("/api/datasets/licensing/options/")
        self.assertEqual(opts_resp.status_code, status.HTTP_200_OK)
        self.assertTrue(any(item["key"] == "MIT-DATA" for item in opts_resp.data["supported_licenses"]))

        # Delete custom license resets default back to CC-BY-4.0
        del_resp = self.client.delete("/api/admin-panel/settings/licensing/licenses/MIT-DATA/")
        self.assertEqual(del_resp.status_code, status.HTTP_200_OK)
        self.assertEqual(del_resp.data["default_license"], "CC-BY-4.0")

    def test_non_admin_cannot_create_custom_license(self):
        self.client.force_authenticate(self.user)
        resp = self.client.post("/api/admin-panel/settings/licensing/licenses/", {
            "name": "Unauthorized License",
        })
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_admin_can_edit_and_deactivate_custom_license(self):
        self.client.force_authenticate(self.admin)
        self.client.post("/api/admin-panel/settings/licensing/licenses/", {
            "key": "CUSTOM-EDIT",
            "name": "Initial Name",
            "short_name": "Initial",
            "description": "Original description",
        })
        self.client.post("/api/admin-panel/settings/licensing/", {
            "default_license": "CUSTOM-EDIT",
        })

        # Edit name & description
        edit_resp = self.client.patch("/api/admin-panel/settings/licensing/licenses/CUSTOM-EDIT/", {
            "name": "Updated License Name",
            "description": "Updated description",
            "is_restricted": True,
        })
        self.assertEqual(edit_resp.status_code, status.HTTP_200_OK)
        self.assertEqual(edit_resp.data["license"]["name"], "Updated License Name")
        self.assertTrue(edit_resp.data["license"]["is_restricted"])

        # Deactivate -> excluded from supported_licenses, default resets to CC-BY-4.0, still in all_licenses
        deact_resp = self.client.patch("/api/admin-panel/settings/licensing/licenses/CUSTOM-EDIT/", {
            "is_active": False,
        })
        self.assertEqual(deact_resp.status_code, status.HTTP_200_OK)
        self.assertFalse(deact_resp.data["license"]["is_active"])
        self.assertEqual(deact_resp.data["default_license"], "CC-BY-4.0")
        self.assertFalse(any(l["key"] == "CUSTOM-EDIT" for l in deact_resp.data["supported_licenses"]))
        self.assertTrue(any(l["key"] == "CUSTOM-EDIT" for l in deact_resp.data["all_licenses"]))

        # Reactivate -> back in supported_licenses
        react_resp = self.client.patch("/api/admin-panel/settings/licensing/licenses/CUSTOM-EDIT/", {
            "is_active": True,
        })
        self.assertEqual(react_resp.status_code, status.HTTP_200_OK)
        self.assertTrue(any(l["key"] == "CUSTOM-EDIT" for l in react_resp.data["supported_licenses"]))
