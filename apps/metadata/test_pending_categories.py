from rest_framework.test import APITestCase
from rest_framework import status

from apps.datasets.factories import make_user
from apps.datasets.models import Dataset
from .models import Category


class DatasetOtherCategoryTests(APITestCase):
    
    def test_new_category_created_as_pending_and_still_usable(self):
        researcher = make_user("ocresearcher", "ocresearcher@aastu.edu.et", role="researcher")
        dataset = Dataset.objects.create(title="OC DS", owner=researcher)

        self.client.force_authenticate(researcher)
        resp = self.client.post(f"/api/metadata/{dataset.id}/attach/", {
            "description": "test data", "other_category": "Quantum Beekeeping",
            
        })
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        category = Category.objects.get(name="Quantum Beekeeping")
        self.assertEqual(category.status, Category.Status.PENDING)
        dataset.refresh_from_db()
        self.assertEqual(dataset.metadata.category, category)

    def test_existing_approved_category_used_via_category_id(self):
        researcher = make_user("ocresearcher2", "ocresearcher2@aastu.edu.et", role="researcher")
        category = Category.objects.create(name="Agriculture", status=Category.Status.APPROVED)
        dataset = Dataset.objects.create(title="OC DS 2", owner=researcher)

        self.client.force_authenticate(researcher)
        resp = self.client.post(f"/api/metadata/{dataset.id}/attach/", {
            "description": "test data", "category_id": category.id, 
        })
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        dataset.refresh_from_db()
        self.assertEqual(dataset.metadata.category, category)

    def test_missing_both_category_fields_rejected(self):
        researcher = make_user("ocresearcher3", "ocresearcher3@aastu.edu.et", role="researcher")
        dataset = Dataset.objects.create(title="OC DS 3", owner=researcher)

        self.client.force_authenticate(researcher)
        resp = self.client.post(f"/api/metadata/{dataset.id}/attach/", {
            "description": "test data", 
        })
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_two_people_suggesting_same_new_name_reuse_one_category(self):
        researcher1 = make_user("ocresearcher4", "ocresearcher4@aastu.edu.et", role="researcher")
        researcher2 = make_user("ocresearcher5", "ocresearcher5@aastu.edu.et", role="researcher")
        dataset1 = Dataset.objects.create(title="OC DS 4", owner=researcher1)
        dataset2 = Dataset.objects.create(title="OC DS 5", owner=researcher2)

        self.client.force_authenticate(researcher1)
        self.client.post(f"/api/metadata/{dataset1.id}/attach/", {
            "description": "a", "other_category": "Marine Robotics", 
        })
        self.client.force_authenticate(researcher2)
        self.client.post(f"/api/metadata/{dataset2.id}/attach/", {
            "description": "b", "other_category": "marine robotics",  # different casing
        })

        self.assertEqual(Category.objects.filter(name__iexact="Marine Robotics").count(), 1)


class ProfileOtherInterestTests(APITestCase):
    def test_add_other_interest_creates_pending_category(self):
        researcher = make_user("piresearcher", "piresearcher@aastu.edu.et", role="researcher")
        self.client.force_authenticate(researcher)
        resp = self.client.post("/api/accounts/profile/interests/other/", {"name": "Applied Cryptozoology"})
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertTrue(resp.data["pending_review"])
        researcher.profile.refresh_from_db()
        self.assertTrue(researcher.profile.interests.filter(name="Applied Cryptozoology").exists())

    def test_add_other_interest_reuses_existing_approved_category(self):
        researcher = make_user("piresearcher2", "piresearcher2@aastu.edu.et", role="researcher")
        Category.objects.create(name="Public Health", status=Category.Status.APPROVED)

        self.client.force_authenticate(researcher)
        resp = self.client.post("/api/accounts/profile/interests/other/", {"name": "Public Health"})
        self.assertFalse(resp.data["pending_review"])
        self.assertEqual(Category.objects.filter(name="Public Health").count(), 1)


class CategoryVisibilityTests(APITestCase):
    def test_pending_category_hidden_from_dropdown(self):
        researcher = make_user("cvresearcher", "cvresearcher@aastu.edu.et", role="researcher")
        Category.objects.create(name="Approved Cat", status=Category.Status.APPROVED)
        Category.objects.create(name="Pending Cat", status=Category.Status.PENDING)

        self.client.force_authenticate(researcher)
        resp = self.client.get("/api/metadata/categories/")
        names = {c["name"] for c in resp.data}
        self.assertIn("Approved Cat", names)
        self.assertNotIn("Pending Cat", names)


class AdminCategoryReviewTests(APITestCase):
    def test_admin_sees_pending_queue(self):
        admin = make_user("acradmin", "acradmin@aastu.edu.et", role="admin")
        researcher = make_user("acrresearcher", "acrresearcher@aastu.edu.et", role="researcher")
        Category.objects.create(name="Needs Review", status=Category.Status.PENDING, suggested_by=researcher)

        self.client.force_authenticate(admin)
        resp = self.client.get("/api/admin-panel/categories/pending/")
        names = {c["name"] for c in resp.data}
        self.assertIn("Needs Review", names)

    def test_approve_makes_category_visible_in_dropdown(self):
        admin = make_user("acaadmin", "acaadmin@aastu.edu.et", role="admin")
        researcher = make_user("acaresearcher", "acaresearcher@aastu.edu.et", role="researcher")
        category = Category.objects.create(name="Newly Approved", status=Category.Status.PENDING, suggested_by=researcher)

        self.client.force_authenticate(admin)
        resp = self.client.post(f"/api/admin-panel/categories/{category.id}/decide/", {"decision": "approve"})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        category.refresh_from_db()
        self.assertEqual(category.status, Category.Status.APPROVED)

        self.client.force_authenticate(researcher)
        list_resp = self.client.get("/api/metadata/categories/")
        names = {c["name"] for c in list_resp.data}
        self.assertIn("Newly Approved", names)

    def test_reject_keeps_category_hidden(self):
        admin = make_user("acrjadmin", "acrjadmin@aastu.edu.et", role="admin")
        researcher = make_user("acrjresearcher", "acrjresearcher@aastu.edu.et", role="researcher")
        category = Category.objects.create(name="Rejected One", status=Category.Status.PENDING, suggested_by=researcher)

        self.client.force_authenticate(admin)
        resp = self.client.post(f"/api/admin-panel/categories/{category.id}/decide/", {"decision": "reject"})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        category.refresh_from_db()
        self.assertEqual(category.status, Category.Status.REJECTED)

        self.client.force_authenticate(researcher)
        list_resp = self.client.get("/api/metadata/categories/")
        names = {c["name"] for c in list_resp.data}
        self.assertNotIn("Rejected One", names)

    def test_researcher_cannot_decide_pending_category(self):
        researcher = make_user("acndresearcher", "acndresearcher@aastu.edu.et", role="researcher")
        category = Category.objects.create(name="Off Limits", status=Category.Status.PENDING)

        self.client.force_authenticate(researcher)
        resp = self.client.post(f"/api/admin-panel/categories/{category.id}/decide/", {"decision": "approve"})
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)