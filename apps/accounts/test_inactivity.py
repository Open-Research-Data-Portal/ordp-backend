from datetime import timedelta

from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from apps.accounts.models import ActivityLog, EmailVerificationToken
from apps.accounts.services import (
    AUTO_DEACTIVATION_ACTION,
    INACTIVITY_REASON,
    MANUAL_DEACTIVATION_ACTION,
    REACTIVATION_ACTION,
    deactivate_inactive_users,
    six_months_before,
)
from apps.datasets.factories import make_user


User = get_user_model()


class UserInactivityTests(APITestCase):
    def test_deactivates_only_users_past_six_calendar_months_and_logs_reason(self):
        now = timezone.now()
        cutoff = six_months_before(now)
        stale = make_user("staleuser", "staleuser@aastu.edu.et")
        recent = make_user("recentuser", "recentuser@aastu.edu.et")
        User.objects.filter(pk=stale.pk).update(last_login=cutoff - timedelta(seconds=1))
        User.objects.filter(pk=recent.pk).update(last_login=cutoff)

        result = deactivate_inactive_users(now=now)

        self.assertEqual(result["affected_count"], 1)
        stale.refresh_from_db()
        recent.refresh_from_db()
        self.assertFalse(stale.is_active)
        self.assertTrue(recent.is_active)
        stale.profile.refresh_from_db()
        self.assertTrue(stale.profile.email_verified)
        event = ActivityLog.objects.get(
            action=AUTO_DEACTIVATION_ACTION,
            target_object=str(stale.id),
        )
        self.assertEqual(event.extra["reason"], INACTIVITY_REASON)
        self.assertEqual(event.extra["last_login"], (cutoff - timedelta(seconds=1)).isoformat())

    def test_never_logged_in_user_uses_join_date_for_inactivity(self):
        now = timezone.now()
        cutoff = six_months_before(now)
        old_user = make_user("neverloginold", "neverloginold@aastu.edu.et")
        new_user = make_user("neverloginnew", "neverloginnew@aastu.edu.et")
        User.objects.filter(pk=old_user.pk).update(last_login=None, date_joined=cutoff - timedelta(seconds=1))
        User.objects.filter(pk=new_user.pk).update(last_login=None, date_joined=cutoff + timedelta(seconds=1))

        result = deactivate_inactive_users(now=now)

        old_user.refresh_from_db()
        new_user.refresh_from_db()
        self.assertFalse(old_user.is_active)
        self.assertTrue(new_user.is_active)
        self.assertEqual(result["affected_count"], 1)

    def test_dry_run_does_not_deactivate_or_log(self):
        now = timezone.now()
        user = make_user("dryrunuser", "dryrunuser@aastu.edu.et")
        User.objects.filter(pk=user.pk).update(last_login=six_months_before(now) - timedelta(days=1))

        result = deactivate_inactive_users(now=now, dry_run=True)

        user.refresh_from_db()
        self.assertTrue(user.is_active)
        self.assertEqual(result["affected_count"], 1)
        self.assertFalse(ActivityLog.objects.filter(action=AUTO_DEACTIVATION_ACTION).exists())

    def test_last_active_admin_is_not_automatically_deactivated(self):
        now = timezone.now()
        admin = make_user("lastadmin", "lastadmin@aastu.edu.et", role="admin")
        User.objects.filter(pk=admin.pk).update(last_login=six_months_before(now) - timedelta(days=1))

        result = deactivate_inactive_users(now=now)

        admin.refresh_from_db()
        self.assertTrue(admin.is_active)
        self.assertEqual(result["affected_count"], 0)

    def test_recent_admin_reactivation_starts_a_fresh_inactivity_window(self):
        now = timezone.now()
        user = make_user("recentreactivation", "recentreactivation@aastu.edu.et")
        User.objects.filter(pk=user.pk).update(last_login=six_months_before(now) - timedelta(days=1))
        ActivityLog.objects.create(
            user=None,
            action=REACTIVATION_ACTION,
            target_object=str(user.id),
            ip_address="127.0.0.1",
            timestamp=now - timedelta(days=1),
        )

        result = deactivate_inactive_users(now=now)

        user.refresh_from_db()
        self.assertTrue(user.is_active)
        self.assertEqual(result["affected_count"], 0)

    def test_inactive_verified_user_gets_inactive_account_response(self):
        user = make_user("inactiveverified", "inactiveverified@aastu.edu.et")
        user.is_active = False
        user.save(update_fields=["is_active"])
        user.profile.email_verified = True
        user.profile.save(update_fields=["email_verified"])

        response = self.client.post("/api/accounts/login/", {
            "identifier": user.email,
            "password": "pw12345!",
        }, format="json")

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(response.data["error"]["code"], "ACCOUNT_INACTIVE")

    def test_unverified_user_still_gets_email_verification_response(self):
        user = User.objects.create_user(
            username="unverifieduser",
            email="unverifieduser@aastu.edu.et",
            password="pw12345!",
            is_active=False,
        )
        EmailVerificationToken.objects.create(
            user=user,
            expires_at=timezone.now() + timedelta(hours=1),
        )

        response = self.client.post("/api/accounts/login/", {
            "identifier": user.email,
            "password": "pw12345!",
        }, format="json")

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(response.data["error"]["code"], "EMAIL_NOT_VERIFIED")

    def test_legacy_admin_created_inactive_account_is_not_misclassified(self):
        user = User.objects.create_user(
            username="legacyinactive",
            email="legacyinactive@aastu.edu.et",
            password="pw12345!",
            is_active=False,
        )
        ActivityLog.objects.create(
            user=None,
            action="user_created",
            target_object=str(user.id),
            ip_address="127.0.0.1",
        )

        response = self.client.post("/api/accounts/login/", {
            "identifier": user.email,
            "password": "pw12345!",
        }, format="json")

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(response.data["error"]["code"], "ACCOUNT_INACTIVE")

    def test_successful_login_updates_last_login(self):
        user = make_user("lastlogintest", "lastlogintest@aastu.edu.et")
        user.profile.email_verified = True
        user.profile.save(update_fields=["email_verified"])

        response = self.client.post("/api/accounts/login/", {
            "identifier": user.email,
            "password": "pw12345!",
        }, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        user.refresh_from_db()
        self.assertIsNotNone(user.last_login)

    def test_old_verification_link_cannot_reactivate_inactive_verified_user(self):
        user = make_user("inactiveverify", "inactiveverify@aastu.edu.et")
        user.is_active = False
        user.save(update_fields=["is_active"])
        user.profile.email_verified = True
        user.profile.save(update_fields=["email_verified"])
        verification = EmailVerificationToken.objects.create(
            user=user,
            expires_at=timezone.now() + timedelta(hours=1),
        )

        response = self.client.post("/api/accounts/verify-email/", {
            "token": str(verification.token),
        }, format="json")

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(response.data["error"]["code"], "ACCOUNT_INACTIVE")
        user.refresh_from_db()
        self.assertFalse(user.is_active)

    def test_first_email_verification_still_activates_unverified_user(self):
        user = User.objects.create_user(
            username="firstverification",
            email="firstverification@aastu.edu.et",
            password="pw12345!",
            is_active=False,
        )
        verification = EmailVerificationToken.objects.create(
            user=user,
            expires_at=timezone.now() + timedelta(hours=1),
        )

        response = self.client.post("/api/accounts/verify-email/", {
            "token": str(verification.token),
        }, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        user.refresh_from_db()
        self.assertTrue(user.is_active)
        self.assertIsNotNone(user.last_login)
        self.assertTrue(user.profile.email_verified)

    def test_admin_sees_reason_and_can_reactivate_auto_deactivated_user(self):
        admin = make_user("inactivityadmin", "inactivityadmin@aastu.edu.et", role="admin")
        target = make_user("inactivevisible", "inactivevisible@aastu.edu.et")
        target.is_active = False
        target.save(update_fields=["is_active"])
        event = ActivityLog.objects.create(
            user=None,
            action=AUTO_DEACTIVATION_ACTION,
            target_object=str(target.id),
            ip_address="127.0.0.1",
            extra={"reason": INACTIVITY_REASON},
        )

        self.client.force_authenticate(admin)
        response = self.client.get("/api/admin-panel/users/inactive/")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        entry = next(item for item in response.data["users"] if item["id"] == target.id)
        self.assertEqual(entry["deactivation_type"], "automatic")
        self.assertEqual(entry["deactivation_reason"], INACTIVITY_REASON)
        self.assertEqual(entry["deactivated_at"], event.timestamp)

        response = self.client.post(f"/api/admin-panel/users/{target.id}/reactivate/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        target.refresh_from_db()
        self.assertTrue(target.is_active)

    def test_manual_deactivation_is_logged_and_existing_reactivation_works(self):
        admin = make_user("manualadmin", "manualadmin@aastu.edu.et", role="admin")
        target = make_user("manualtarget", "manualtarget@aastu.edu.et")
        self.client.force_authenticate(admin)

        response = self.client.post(
            f"/api/admin-panel/users/{target.id}/deactivate/",
            {"reason": "Policy review"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        target.refresh_from_db()
        self.assertFalse(target.is_active)
        event = ActivityLog.objects.get(
            action=MANUAL_DEACTIVATION_ACTION,
            target_object=str(target.id),
        )
        self.assertEqual(event.user, admin)
        self.assertEqual(event.extra["reason"], "Policy review")

        response = self.client.get("/api/admin-panel/users/inactive/")
        entry = next(item for item in response.data["users"] if item["id"] == target.id)
        self.assertEqual(entry["deactivation_type"], "manual")
        self.assertEqual(entry["deactivation_reason"], "Policy review")

        response = self.client.post(f"/api/admin-panel/users/{target.id}/reactivate/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        target.refresh_from_db()
        self.assertTrue(target.is_active)