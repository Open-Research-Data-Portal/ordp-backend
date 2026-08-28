from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase
from rest_framework import status

from apps.accounts.models import UserProfile, UserRole, College, Department
from apps.notifications.models import Notification

User = get_user_model()


def make_incomplete_user(username, email):
    user = User.objects.create_user(username=username, email=email, password="pw12345!")
    profile = user.profile
    profile.full_name = username.title()
    profile.save()
    return user


class ProfileCompletionGrantsResearcherTests(APITestCase):
    def setUp(self):
        self.college = College.objects.create(name="College of Testing")
        self.department = Department.objects.create(name="Dept of Testing", college=self.college)

    def test_completing_profile_grants_researcher_immediately(self):
        user = make_incomplete_user("pcguser", "pcguser@aastu.edu.et")
        self.client.force_authenticate(user)

        resp = self.client.patch("/api/accounts/profile/complete/", {
            "academia": "researcher", "department": str(self.department.id), "terms_accepted": True,
        })
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

        user.profile.refresh_from_db()
        self.assertTrue(user.profile.has_role("researcher"))
        self.assertTrue(UserRole.objects.filter(profile=user.profile, role="researcher").exists())

    def test_completing_profile_notifies_user_not_admin(self):
        user = make_incomplete_user("pcnuser", "pcnuser@aastu.edu.et")
        self.client.force_authenticate(user)
        self.client.patch("/api/accounts/profile/complete/", {
            "academia": "researcher", "department": str(self.department.id), "terms_accepted": True,
        })
        self.assertTrue(Notification.objects.filter(
            user=user, notification_type=Notification.NotificationType.RESEARCHER_APPROVED,
        ).exists())

    def test_incomplete_profile_does_not_grant_researcher(self):
        user = make_incomplete_user("ipguser", "ipguser@aastu.edu.et")
        self.client.force_authenticate(user)
        self.client.patch("/api/accounts/profile/complete/", {"academia": "researcher"})  # no department, no terms

        user.profile.refresh_from_db()
        self.assertFalse(user.profile.has_role("researcher"))

    def test_missing_terms_does_not_grant_researcher(self):
        user = make_incomplete_user("mtguser", "mtguser@aastu.edu.et")
        self.client.force_authenticate(user)
        self.client.patch("/api/accounts/profile/complete/", {
            "academia": "researcher", "department": str(self.department.id),
        })  # terms_accepted omitted

        user.profile.refresh_from_db()
        self.assertFalse(user.profile.has_role("researcher"))

    def test_completed_researcher_can_immediately_upload(self):
        user = make_incomplete_user("cruuser", "cruuser@aastu.edu.et")
        self.client.force_authenticate(user)
        self.client.patch("/api/accounts/profile/complete/", {
            "academia": "researcher", "department": str(self.department.id), "terms_accepted": True,
        })

        resp = self.client.post("/api/datasets/upload/init/", {"title": "Immediate Upload Test"})
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)

    def test_re_saving_complete_profile_does_not_duplicate_role(self):
        user = make_incomplete_user("dupuser", "dupuser@aastu.edu.et")
        self.client.force_authenticate(user)
        payload = {"academia": "researcher", "department": str(self.department.id), "terms_accepted": True}
        self.client.patch("/api/accounts/profile/complete/", payload)
        self.client.patch("/api/accounts/profile/complete/", {"bio": "updated bio"})

        self.assertEqual(UserRole.objects.filter(profile=user.profile, role="researcher").count(), 1)

    def test_get_no_longer_exposes_pending_flag(self):
        user = make_incomplete_user("nopuser", "nopuser@aastu.edu.et")
        self.client.force_authenticate(user)
        resp = self.client.get("/api/accounts/profile/complete/")
        self.assertNotIn("researcher_request_pending", resp.data)
        self.assertIn("roles", resp.data)