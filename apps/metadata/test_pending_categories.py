import uuid

from rest_framework.test import APITestCase
from rest_framework import status

from apps.datasets.factories import make_user
from apps.datasets.models import Dataset
from apps.notifications.models import Notification
from .models import Category, Metadata


class DatasetOtherCategoryTests(APITestCase):

    def test_new_category_is_approved_and_visible_to_other_users(self):
        researcher = make_user("ocresearcher", "ocresearcher@aastu.edu.et", role="researcher")
        dataset = Dataset.objects.create(title="OC DS", owner=researcher)

        self.client.force_authenticate(researcher)
        resp = self.client.post(f"/api/metadata/{dataset.id}/attach/", {
            "description": "test data", "other_category": "Quantum Beekeeping",
        })
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        category = Category.objects.get(name="Quantum Beekeeping")
        self.assertEqual(category.status, Category.Status.APPROVED)
        dataset.refresh_from_db()
        self.assertEqual(dataset.metadata.category, category)

        other_researcher = make_user("ocother", "ocother@aastu.edu.et", role="researcher")
        self.client.force_authenticate(other_researcher)
        visible = self.client.get("/api/metadata/categories/")
        self.assertIn("Quantum Beekeeping", {item["name"] for item in visible.data})

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
            "description": "b", "other_category": "marine robotics",
        })

        self.assertEqual(Category.objects.filter(name__iexact="Marine Robotics").count(), 1)

    def test_near_duplicate_can_be_created_and_is_immediately_approved(self):
        researcher = make_user("ocnearresearcher", "ocnearresearcher@aastu.edu.et", role="researcher")
        approved = Category.objects.create(
            name="Climate Science", status=Category.Status.APPROVED
        )
        dataset = Dataset.objects.create(title="Near Duplicate", owner=researcher)

        self.client.force_authenticate(researcher)
        resp = self.client.post(f"/api/metadata/{dataset.id}/attach/", {
            "description": "test data", "other_category": "Climate Sci",
        })

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        suggestion = Category.objects.get(name="Climate Sci")
        self.assertEqual(suggestion.status, Category.Status.APPROVED)
        self.assertNotEqual(suggestion, approved)
        dataset.refresh_from_db()
        self.assertEqual(dataset.metadata.category, suggestion)

    def test_reusing_exact_pending_category_promotes_existing_record(self):
        researcher = make_user("ocpendingreuse", "ocpendingreuse@aastu.edu.et", role="researcher")
        dataset = Dataset.objects.create(title="Pending Reuse", owner=researcher)
        category = Category.objects.create(
            name="Marine Biology", status=Category.Status.PENDING,
            origin=Category.Origin.DATASET_OTHER,
        )

        self.client.force_authenticate(researcher)
        resp = self.client.post(f"/api/metadata/{dataset.id}/attach/", {
            "description": "test data", "other_category": "marine biology",
        })

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(Category.objects.filter(name__iexact="Marine Biology").count(), 1)
        category.refresh_from_db()
        self.assertEqual(category.status, Category.Status.APPROVED)
        dataset.refresh_from_db()
        self.assertEqual(dataset.metadata.category, category)


class ProfileOtherInterestTests(APITestCase):
    def test_add_other_interest_creates_approved_category(self):
        researcher = make_user("piresearcher", "piresearcher@aastu.edu.et", role="researcher")
        self.client.force_authenticate(researcher)
        resp = self.client.post("/api/accounts/profile/interests/other/", {"name": "Applied Cryptozoology"})
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)
        self.assertFalse(resp.data["pending_review"])
        category = Category.objects.get(name="Applied Cryptozoology")
        self.assertEqual(category.status, Category.Status.APPROVED)
        researcher.profile.refresh_from_db()
        self.assertTrue(researcher.profile.interests.filter(name="Applied Cryptozoology").exists())

    def test_add_other_interest_reuses_existing_approved_category(self):
        researcher = make_user("piresearcher2", "piresearcher2@aastu.edu.et", role="researcher")
        Category.objects.create(name="Public Health", status=Category.Status.APPROVED)

        self.client.force_authenticate(researcher)
        resp = self.client.post("/api/accounts/profile/interests/other/", {"name": "Public Health"})
        self.assertFalse(resp.data["pending_review"])
        self.assertEqual(Category.objects.filter(name="Public Health").count(), 1)


class ResearchInterestOnboardingTests(APITestCase):
    url = "/api/accounts/profile/complete/"
    interest_list_url = "/api/metadata/categories/interests/"
    unavailable_message = (
        "One or more selected research interests are no longer available. "
        "Please refresh the list and select valid interests."
    )

    def setUp(self):
        self.researcher = make_user(
            "interestonboarding",
            "interestonboarding@aastu.edu.et",
            role="researcher",
        )
        self.client.force_authenticate(self.researcher)

    def test_interest_list_returns_approved_categories_only(self):
        standard = Category.objects.create(
            name="Approved Standard",
            status=Category.Status.APPROVED,
            origin=Category.Origin.STANDARD,
        )
        interest_other = Category.objects.create(
            name="Approved Interest Other",
            status=Category.Status.APPROVED,
            origin=Category.Origin.INTEREST_OTHER,
        )
        dataset_other = Category.objects.create(
            name="Approved Dataset Other",
            status=Category.Status.APPROVED,
            origin=Category.Origin.DATASET_OTHER,
        )
        pending = Category.objects.create(
            name="Pending Interest",
            status=Category.Status.PENDING,
        )
        rejected = Category.objects.create(
            name="Rejected Interest",
            status=Category.Status.REJECTED,
        )

        response = self.client.get(self.interest_list_url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            {str(item["id"]) for item in response.data},
            {str(standard.id), str(interest_other.id), str(dataset_other.id)},
        )
        listed_ids = {str(item["id"]) for item in response.data}
        self.assertNotIn(str(pending.id), listed_ids)
        self.assertNotIn(str(rejected.id), listed_ids)

    def test_approved_standard_category_can_be_submitted(self):
        category = Category.objects.create(
            name="Standard Research",
            status=Category.Status.APPROVED,
            origin=Category.Origin.STANDARD,
        )

        response = self.client.patch(
            self.url,
            {"interests": [str(category.id)]},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn(category, self.researcher.profile.interests.all())
        self.assertTrue(self.researcher.profile.is_profile_complete())
        self.assertTrue(self.researcher.profile.can_upload_datasets)

    def test_approved_interest_other_category_can_be_submitted(self):
        category = Category.objects.create(
            name="Custom Research",
            status=Category.Status.APPROVED,
            origin=Category.Origin.INTEREST_OTHER,
        )

        response = self.client.patch(
            self.url,
            {"interests": [str(category.id)]},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn(category, self.researcher.profile.interests.all())

    def test_pending_and_rejected_categories_cannot_be_submitted(self):
        for category_status in (Category.Status.PENDING, Category.Status.REJECTED):
            with self.subTest(category_status=category_status):
                category = Category.objects.create(
                    name=f"{category_status.title()} Research",
                    status=category_status,
                )

                response = self.client.patch(
                    self.url,
                    {"interests": [str(category.id)]},
                    format="json",
                )

                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertIn(self.unavailable_message, str(response.data["interests"]))
                self.assertFalse(
                    self.researcher.profile.interests.filter(id=category.id).exists()
                )

    def test_stale_category_uuid_returns_actionable_validation_error(self):
        response = self.client.patch(
            self.url,
            {"interests": [str(uuid.uuid4())]},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn(self.unavailable_message, str(response.data["interests"]))

    def test_invalid_uuid_keeps_uuid_validation_error(self):
        response = self.client.patch(
            self.url,
            {"interests": ["not-a-uuid"]},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertNotIn(self.unavailable_message, str(response.data["interests"]))

    def test_every_listed_interest_is_accepted_by_profile_completion(self):
        categories = [
            Category.objects.create(
                name=f"Listed Interest {origin}",
                status=Category.Status.APPROVED,
                origin=origin,
            )
            for origin in Category.Origin.values
        ]
        listed = self.client.get(self.interest_list_url)
        listed_ids = [str(item["id"]) for item in listed.data]

        response = self.client.patch(
            self.url,
            {"interests": listed_ids},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            set(self.researcher.profile.interests.values_list("id", flat=True)),
            {category.id for category in categories},
        )


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

    def test_user_gets_ranked_suggestions_from_approved_categories_only(self):
        researcher = make_user("cvsuggest", "cvsuggest@aastu.edu.et", role="researcher")
        target = Category.objects.create(name="Climate Science", status=Category.Status.APPROVED)
        Category.objects.create(name="Agriculture", status=Category.Status.APPROVED)
        Category.objects.create(name="Climate Sci", status=Category.Status.PENDING)

        self.client.force_authenticate(researcher)
        resp = self.client.get("/api/metadata/categories/suggestions/?q=Climate%20Sci")

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data[0]["id"], str(target.id))
        self.assertEqual(resp.data[0]["name"], "Climate Science")
        self.assertNotIn("Climate Sci", {item["name"] for item in resp.data})

    def test_category_suggestions_require_authentication(self):
        resp = self.client.get("/api/metadata/categories/suggestions/?q=Climate")
        self.assertEqual(resp.status_code, status.HTTP_401_UNAUTHORIZED)


class AdminCategoryReviewTests(APITestCase):
    def test_admin_sees_pending_queue(self):
        admin = make_user("acradmin", "acradmin@aastu.edu.et", role="admin")
        researcher = make_user("acrresearcher", "acrresearcher@aastu.edu.et", role="researcher")
        Category.objects.create(name="Needs Review", status=Category.Status.PENDING, suggested_by=researcher)

        self.client.force_authenticate(admin)
        resp = self.client.get("/api/admin-panel/categories/pending/")
        names = {c["name"] for c in resp.data}
        self.assertIn("Needs Review", names)

    def test_pending_queue_ranks_similar_approved_categories(self):
        admin = make_user("acrsimadmin", "acrsimadmin@aastu.edu.et", role="admin")
        target = Category.objects.create(name="Climate Science", status=Category.Status.APPROVED)
        Category.objects.create(name="Agriculture", status=Category.Status.APPROVED)
        Category.objects.create(name="Climate", status=Category.Status.PENDING)

        self.client.force_authenticate(admin)
        resp = self.client.get("/api/admin-panel/categories/pending/")

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        climate = next(item for item in resp.data if item["name"] == "Climate")
        self.assertEqual(climate["similar_existing"][0]["id"], str(target.id))

    def test_admin_can_search_approved_merge_targets(self):
        admin = make_user("acrsrchadmin", "acrsrchadmin@aastu.edu.et", role="admin")
        Category.objects.create(name="Climate Science", status=Category.Status.APPROVED)
        Category.objects.create(name="Agriculture", status=Category.Status.APPROVED)
        Category.objects.create(name="Climate Pending", status=Category.Status.PENDING)

        self.client.force_authenticate(admin)
        resp = self.client.get("/api/admin-panel/categories/approved/?search=climate")

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual([item["name"] for item in resp.data], ["Climate Science"])

    def test_approve_makes_category_visible_in_dropdown(self):
        admin = make_user("acaadmin", "acaadmin@aastu.edu.et", role="admin")
        researcher = make_user("acaresearcher", "acaresearcher@aastu.edu.et", role="researcher")
        category = Category.objects.create(name="Newly Approved", status=Category.Status.PENDING, suggested_by=researcher)
        dataset = Dataset.objects.create(title="Uses Suggested Category", owner=researcher)
        Metadata.objects.create(dataset=dataset, description="x", category=category)
        researcher.profile.interests.add(category)

        self.client.force_authenticate(admin)
        resp = self.client.post(f"/api/admin-panel/categories/{category.id}/decide/", {"decision": "approve"})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertTrue(Notification.objects.filter(
            user=researcher,
            notification_type=Notification.NotificationType.CATEGORY_DECISION,
            message__contains="was approved",
        ).exists())
        category.refresh_from_db()
        self.assertEqual(category.status, Category.Status.APPROVED)
        dataset.refresh_from_db()
        self.assertEqual(dataset.metadata.category, category)
        self.assertIn(category, researcher.profile.interests.all())

        self.client.force_authenticate(researcher)
        bell_resp = self.client.get("/api/notifications/bell/")
        self.assertEqual(bell_resp.status_code, status.HTTP_200_OK)
        self.assertTrue(any(
            item["notification_type"] == Notification.NotificationType.CATEGORY_DECISION
            for item in bell_resp.data["notifications"]
        ))
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
        self.assertTrue(Notification.objects.filter(
            user=researcher,
            notification_type=Notification.NotificationType.CATEGORY_DECISION,
            message__contains="not approved",
        ).exists())
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

    def test_merge_moves_datasets_and_interests(self):
        admin = make_user("acmadmin", "acmadmin@aastu.edu.et", role="admin")
        researcher = make_user("acmres", "acmres@aastu.edu.et", role="researcher")
        target = Category.objects.create(name="Climate Science", status=Category.Status.APPROVED)
        dup = Category.objects.create(name="Climate", status=Category.Status.PENDING, suggested_by=researcher)
        researcher.profile.interests.add(dup)
        dataset = Dataset.objects.create(title="D", owner=researcher)
        Metadata.objects.create(dataset=dataset, description="x", category=dup)

        self.client.force_authenticate(admin)
        resp = self.client.post(
            f"/api/admin-panel/categories/{dup.id}/decide/",
            {"decision": "merge", "merge_into": str(target.id)},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(Notification.objects.filter(
            user=researcher,
            notification_type=Notification.NotificationType.CATEGORY_DECISION,
            message__contains="merged into",
        ).exists())
        dataset.refresh_from_db()
        self.assertEqual(dataset.metadata.category, target)
        self.assertIn(target, researcher.profile.interests.all())
        self.assertFalse(Category.objects.filter(id=dup.id).exists())

    def test_reject_in_use_category_requires_approved_replacement(self):
        admin = make_user("acuadmin", "acuadmin@aastu.edu.et", role="admin")
        researcher = make_user("acures", "acures@aastu.edu.et", role="researcher")
        cat = Category.objects.create(name="InUse", status=Category.Status.PENDING, suggested_by=researcher)
        ds = Dataset.objects.create(title="D2", owner=researcher)
        Metadata.objects.create(dataset=ds, description="x", category=cat)

        self.client.force_authenticate(admin)
        resp = self.client.post(f"/api/admin-panel/categories/{cat.id}/decide/", {"decision": "reject"})
        self.assertEqual(resp.status_code, 400)
        cat.refresh_from_db()
        self.assertEqual(cat.status, Category.Status.PENDING)

    def test_reject_in_use_category_reassigns_datasets_to_replacement(self):
        admin = make_user("acurepladmin", "acurepladmin@aastu.edu.et", role="admin")
        researcher = make_user("acureplresearcher", "acureplresearcher@aastu.edu.et", role="researcher")
        rejected = Category.objects.create(
            name="Invalid Category", status=Category.Status.PENDING, suggested_by=researcher
        )
        replacement = Category.objects.create(
            name="Approved Replacement", status=Category.Status.APPROVED
        )
        dataset = Dataset.objects.create(title="Replacement DS", owner=researcher)
        Metadata.objects.create(dataset=dataset, description="x", category=rejected)
        researcher.profile.interests.add(rejected)

        self.client.force_authenticate(admin)
        resp = self.client.post(
            f"/api/admin-panel/categories/{rejected.id}/decide/",
            {
                "decision": "reject",
                "replacement_category_id": str(replacement.id),
            },
        )

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data["replacement_category"]["id"], str(replacement.id))
        rejected.refresh_from_db()
        self.assertEqual(rejected.status, Category.Status.REJECTED)
        dataset.refresh_from_db()
        self.assertEqual(dataset.metadata.category, replacement)
        self.assertFalse(researcher.profile.interests.filter(id=rejected.id).exists())

    def test_reject_in_use_category_rejects_unapproved_replacement(self):
        admin = make_user("acuinvadmin", "acuinvadmin@aastu.edu.et", role="admin")
        researcher = make_user("acuinvresearcher", "acuinvresearcher@aastu.edu.et", role="researcher")
        rejected = Category.objects.create(name="Pending Source", status=Category.Status.PENDING)
        unapproved = Category.objects.create(name="Pending Target", status=Category.Status.PENDING)
        dataset = Dataset.objects.create(title="Invalid Replacement DS", owner=researcher)
        Metadata.objects.create(dataset=dataset, description="x", category=rejected)

        self.client.force_authenticate(admin)
        resp = self.client.post(
            f"/api/admin-panel/categories/{rejected.id}/decide/",
            {
                "decision": "reject",
                "replacement_category_id": str(unapproved.id),
            },
        )

        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        rejected.refresh_from_db()
        self.assertEqual(rejected.status, Category.Status.PENDING)