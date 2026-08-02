from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase
from rest_framework import status

from .models import UserProfile, ResearcherRequest
from apps.notifications.models import Notification
from apps.datasets.factories import make_user, make_college_and_department

User = get_user_model()


def make_public_user(username, email):
    user = User.objects.create_user(username=username, email=email, password="pw12345!")
    UserProfile.objects.create(user=user, full_name=username.title())  # role defaults to public
    return user


def make_admin(username, email):
    user = User.objects.create_user(username=username, email=email, password="pw12345!")
    UserProfile.objects.create(user=user, full_name=username.title(), role=UserProfile.Role.ADMIN)
    return user


def make_checker(username, email):
    user = User.objects.create_user(username=username, email=email, password="pw12345!")
    UserProfile.objects.create(user=user, full_name=username.title(), role=UserProfile.Role.CHECKER)
    return user


def valid_profile_payload(department):
    return {"academia": "researcher", "department": str(department.id), "terms_accepted": True}


class ProfileCompletionCreatesRequestTests(APITestCase):
    def setUp(self):
        self.user = make_user("newpublic", "newpublic@aastu.edu.et", role="public")
        _, self.department = make_college_and_department()
        self.client.force_authenticate(self.user)
        self.payload = {"academia": "researcher", "department": str(self.department.id), "terms_accepted": True}
    def test_new_registrant_defaults_to_public(self):
        self.assertEqual(self.user.profile.role, UserProfile.Role.PUBLIC)

    def test_new_user_defaults_to_public_role(self):
        self.assertEqual(self.user.profile.role, UserProfile.Role.PUBLIC)

    def test_submitting_complete_profile_does_not_auto_promote(self):
        self.client.patch("/api/accounts/profile/complete/", valid_profile_payload(self.department))
        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.profile.role, UserProfile.Role.PUBLIC) 

    def test_submitting_complete_profile_creates_pending_request(self):
        self.client.patch("/api/accounts/profile/complete/", self.payload)
        self.assertTrue(ResearcherRequest.objects.filter(
            user=self.user, status=ResearcherRequest.Status.PENDING
        ).exists())

    def test_get_reflects_pending_flag(self):
        self.client.patch("/api/accounts/profile/complete/", self.payload)
        resp = self.client.get("/api/accounts/profile/complete/")
        self.assertTrue(resp.data["researcher_request_pending"])
        self.assertEqual(resp.data["role"], UserProfile.Role.PUBLIC)

    def test_get_reflects_pending_flag_after_submission(self):
        self.client.patch("/api/accounts/profile/complete/", valid_profile_payload(self.department))
        resp = self.client.get("/api/accounts/profile/complete/")
        self.assertTrue(resp.data["researcher_request_pending"])
        self.assertEqual(resp.data["role"], UserProfile.Role.PUBLIC)

    def test_get_shows_no_pending_before_submission(self):
        resp = self.client.get("/api/accounts/profile/complete/")
        self.assertFalse(resp.data["researcher_request_pending"])

    def test_incomplete_submission_creates_no_request(self):
        self.client.patch("/api/accounts/profile/complete/", {"academia": "researcher"})  
        self.assertFalse(ResearcherRequest.objects.filter(user=self.user).exists())
    def test_missing_terms_acceptance_creates_no_request(self):
        self.client.patch("/api/accounts/profile/complete/", {
            "academia": "researcher", "department": str(self.department.id), "terms_accepted": False,
        })
        self.assertFalse(ResearcherRequest.objects.filter(user=self.user).exists())

    def test_public_user_cannot_init_upload_while_pending(self):
        self.client.patch("/api/accounts/profile/complete/", valid_profile_payload(self.department))
        resp = self.client.post("/api/datasets/upload/init/", {"title": "Should Fail"})
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_public_user_cannot_init_upload_before_submitting_anything(self):
        resp = self.client.post("/api/datasets/upload/init/", {"title": "Should Also Fail"})
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
class AdminApprovalTests(APITestCase):
    def setUp(self):
        self.user = make_public_user("pendinguser", "pendinguser@aastu.edu.et")
        self.admin = make_admin("adminuser", "adminuser@aastu.edu.et")
        self.checker = make_checker("checkeruser", "checkeruser@aastu.edu.et")
        _, self.department = make_college_and_department()
        
        self.client.force_authenticate(self.user)
        self.client.patch("/api/accounts/profile/complete/", {
            "academia": "researcher", "department": str(self.department.id), "terms_accepted": True,
        })
        self.request_obj = ResearcherRequest.objects.get(user=self.user)

    def test_checker_cannot_view_queue(self):
        self.client.force_authenticate(self.checker)
        resp = self.client.get("/api/admin-panel/researcher-requests/queue/")
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_checker_cannot_decide_researcher_request(self):
        self.client.force_authenticate(self.checker)
        resp = self.client.post(
            f"/api/admin-panel/researcher-requests/{self.request_obj.id}/decide/", {"decision": "approve"}
        )
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.profile.role, UserProfile.Role.PUBLIC)

    def test_plain_researcher_cannot_decide(self):
        other_researcher = make_user("otherresearcher", "otherresearcher@aastu.edu.et")
        self.client.force_authenticate(other_researcher)
        resp = self.client.post(
            f"/api/admin-panel/researcher-requests/{self.request_obj.id}/decide/", {"decision": "approve"}
        )
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_admin_sees_pending_request_in_queue(self):
        self.client.force_authenticate(self.admin)
        resp = self.client.get("/api/admin-panel/researcher-requests/queue/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(len(resp.data), 1)
        self.assertEqual(resp.data[0]["email"], self.user.email)

    def test_admin_approval_promotes_role(self):
        self.client.force_authenticate(self.admin)
        resp = self.client.post(
            f"/api/admin-panel/researcher-requests/{self.request_obj.id}/decide/", {"decision": "approve"}
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.profile.role, UserProfile.Role.RESEARCHER)

        self.request_obj.refresh_from_db()
        self.assertEqual(self.request_obj.status, ResearcherRequest.Status.APPROVED)
        self.assertEqual(self.request_obj.decided_by, self.admin)

    def test_admin_approval_notifies_user(self):
        self.client.force_authenticate(self.admin)
        self.client.post(f"/api/admin-panel/researcher-requests/{self.request_obj.id}/decide/", {"decision": "approve"})
        self.assertTrue(Notification.objects.filter(
            user=self.user, notification_type=Notification.NotificationType.RESEARCHER_APPROVED
        ).exists())

    def test_approved_user_can_now_init_upload(self):
        self.client.force_authenticate(self.admin)
        self.client.post(f"/api/admin-panel/researcher-requests/{self.request_obj.id}/decide/", {"decision": "approve"})

        self.client.force_authenticate(self.user)
        resp = self.client.post("/api/datasets/upload/init/", {"title": "Now Allowed"})
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)

    def test_rejection_without_reason_is_blocked(self):
        self.client.force_authenticate(self.admin)
        resp = self.client.post(
            f"/api/admin-panel/researcher-requests/{self.request_obj.id}/decide/", {"decision": "reject"}
        )
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_rejection_keeps_public_and_notifies_with_reason(self):
        self.client.force_authenticate(self.admin)
        resp = self.client.post(
            f"/api/admin-panel/researcher-requests/{self.request_obj.id}/decide/",
            {"decision": "reject", "reason": "Department not recognized — please clarify."},
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

        self.user.profile.refresh_from_db()
        self.assertEqual(self.user.profile.role, UserProfile.Role.PUBLIC)

        self.request_obj.refresh_from_db()
        self.assertEqual(self.request_obj.status, ResearcherRequest.Status.REJECTED)

        notif = Notification.objects.get(user=self.user, notification_type=Notification.NotificationType.RESEARCHER_REJECTED)
        self.assertIn("Department not recognized", notif.reason)

    def test_rejected_user_still_blocked_from_upload(self):
        self.client.force_authenticate(self.admin)
        self.client.post(
            f"/api/admin-panel/researcher-requests/{self.request_obj.id}/decide/",
            {"decision": "reject", "reason": "Insufficient information."},
        )
        self.client.force_authenticate(self.user)
        resp = self.client.post("/api/datasets/upload/init/", {"title": "Still Blocked"})
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_resubmission_after_rejection_resets_to_pending(self):
        self.client.force_authenticate(self.admin)
        self.client.post(
            f"/api/admin-panel/researcher-requests/{self.request_obj.id}/decide/",
            {"decision": "reject", "reason": "Try again with more detail."},
        )
        self.client.force_authenticate(self.user)
        self.client.patch("/api/accounts/profile/complete/", valid_profile_payload(self.department))

        self.request_obj.refresh_from_db()
        self.assertEqual(self.request_obj.status, ResearcherRequest.Status.PENDING)
        self.assertEqual(self.request_obj.reason, "")
        self.assertIsNone(self.request_obj.decided_by)