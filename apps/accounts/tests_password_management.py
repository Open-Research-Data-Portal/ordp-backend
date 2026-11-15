from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import make_password
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase
from rest_framework_simplejwt.token_blacklist.models import (
    BlacklistedToken,
    OutstandingToken,
)
from rest_framework_simplejwt.tokens import RefreshToken

from .models import BlockedCredential, PasswordResetToken


User = get_user_model()
VALID_PASSWORD = "Raven!Cobalt7421"


class BlockedCredentialPasswordTests(APITestCase):
    def test_block_user_creates_30_day_email_and_username_blocks_only(self):
        user = User.objects.create_user(
            username="blockedperson",
            email="blockedperson@aastu.edu.et",
            password=VALID_PASSWORD,
        )
        before = timezone.now()

        expires_at = BlockedCredential.block_user(user)

        self.assertAlmostEqual(
            (expires_at - before).total_seconds(),
            timedelta(days=30).total_seconds(),
            delta=2,
        )
        self.assertEqual(
            set(
                BlockedCredential.objects.filter(
                    value__in=("blockedperson@aastu.edu.et", "blockedperson")
                ).values_list("credential_type", flat=True)
            ),
            {BlockedCredential.CredentialType.EMAIL, BlockedCredential.CredentialType.USERNAME},
        )
        self.assertFalse(
            BlockedCredential.objects.filter(credential_type="password_hash").exists()
        )

    def test_existing_restriction_expiry_is_not_changed(self):
        original_expiry = timezone.now() + timedelta(days=12)
        existing = BlockedCredential.objects.create(
            credential_type=BlockedCredential.CredentialType.EMAIL,
            value="existing@aastu.edu.et",
            expires_at=original_expiry,
        )
        user = User.objects.create_user(
            username="existinguser",
            email="existing@aastu.edu.et",
            password=VALID_PASSWORD,
        )

        BlockedCredential.block_user(user)

        existing.refresh_from_db()
        self.assertEqual(existing.expires_at, original_expiry)

    def test_registration_does_not_reject_globally_blocked_password(self):
        BlockedCredential.objects.create(
            credential_type="password_hash",
            value=make_password(VALID_PASSWORD),
            expires_at=timezone.now() + timedelta(days=10),
        )

        response = self.client.post(
            reverse("register"),
            {
                "full_name": "New Researcher",
                "email": "newresearcher@aastu.edu.et",
                "username": "newresearcher",
                "password": VALID_PASSWORD,
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(User.objects.filter(username="newresearcher").exists())


class PasswordResetCompatibilityTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="resetperson",
            email="resetperson@aastu.edu.et",
            password="Original!Password94",
        )
        self.confirm_url = reverse("password_reset_confirm")

    def test_reset_remains_single_use_and_revokes_outstanding_refresh_tokens(self):
        legacy_hash = make_password(VALID_PASSWORD)
        legacy_block = BlockedCredential.objects.create(
            credential_type="password_hash",
            value=legacy_hash,
            expires_at=timezone.now() + timedelta(days=10),
        )
        old_reset = PasswordResetToken.objects.create(
            user=self.user,
            expires_at=timezone.now() + timedelta(hours=24),
        )
        current_reset = PasswordResetToken.objects.create(
            user=self.user,
            expires_at=timezone.now() + timedelta(hours=24),
        )
        old_reset.is_used = True
        old_reset.save(update_fields=["is_used"])
        refresh = RefreshToken.for_user(self.user)
        outstanding = OutstandingToken.objects.get(token=str(refresh))

        response = self.client.post(
            self.confirm_url,
            {
                "token": str(current_reset.token),
                "new_password": VALID_PASSWORD,
                "confirm_password": VALID_PASSWORD,
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            response.data,
            {"detail": "Password reset successful. Please log in with your new password."},
        )
        self.user.refresh_from_db()
        current_reset.refresh_from_db()
        self.assertTrue(self.user.check_password(VALID_PASSWORD))
        self.assertTrue(current_reset.is_used)
        self.assertTrue(BlacklistedToken.objects.filter(token=outstanding).exists())
        legacy_block.refresh_from_db()
        self.assertEqual(legacy_block.value, legacy_hash)

        reused_response = self.client.post(
            self.confirm_url,
            {
                "token": str(current_reset.token),
                "new_password": "Other!Password975",
                "confirm_password": "Other!Password975",
            },
            format="json",
        )
        self.assertEqual(reused_response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_new_reset_request_invalidates_previous_unused_tokens(self):
        previous = PasswordResetToken.objects.create(
            user=self.user,
            expires_at=timezone.now() + timedelta(hours=24),
        )

        with self.settings(FRONTEND_URL="https://frontend.example"):
            with patch("apps.accounts.views.EmailMultiAlternatives.send"):
                response = self.client.post(
                    reverse("password_reset_request"),
                    {"email": self.user.email},
                    format="json",
                )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        previous.refresh_from_db()
        self.assertTrue(previous.is_used)
        self.assertEqual(
            PasswordResetToken.objects.filter(user=self.user, is_used=False).count(),
            1,
        )

    def test_reset_request_keeps_generic_response_for_unknown_email(self):
        response = self.client.post(
            reverse("password_reset_request"),
            {"email": "unknown@aastu.edu.et"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            response.data,
            {"detail": "If an account exists with that email, a reset link has been sent."},
        )

    def test_expired_reset_token_is_rejected(self):
        reset_token = PasswordResetToken.objects.create(
            user=self.user,
            expires_at=timezone.now() - timedelta(seconds=1),
        )

        response = self.client.post(
            self.confirm_url,
            {
                "token": str(reset_token.token),
                "new_password": VALID_PASSWORD,
                "confirm_password": VALID_PASSWORD,
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("Original!Password94"))


class ChangePasswordTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="changeperson",
            email="changeperson@aastu.edu.et",
            password="Original!Password94",
        )
        self.url = reverse("change_password")

    def test_change_password_requires_authentication(self):
        response = self.client.post(
            self.url,
            {
                "current_password": "Original!Password94",
                "new_password": VALID_PASSWORD,
                "confirm_password": VALID_PASSWORD,
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_change_password_checks_current_password_and_revokes_refresh_tokens(self):
        other_user = User.objects.create_user(
            username="anotherperson",
            email="anotherperson@aastu.edu.et",
            password="Original!Password94",
        )
        self.client.force_authenticate(self.user)
        refresh = RefreshToken.for_user(self.user)
        outstanding = OutstandingToken.objects.get(token=str(refresh))

        invalid_response = self.client.post(
            self.url,
            {
                "current_password": "wrong-current-password",
                "new_password": VALID_PASSWORD,
                "confirm_password": VALID_PASSWORD,
            },
            format="json",
        )
        self.assertEqual(invalid_response.status_code, status.HTTP_400_BAD_REQUEST)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("Original!Password94"))
        self.assertFalse(BlacklistedToken.objects.filter(token=outstanding).exists())

        response = self.client.post(
            self.url,
            {
                "current_password": "Original!Password94",
                "new_password": VALID_PASSWORD,
                "confirm_password": VALID_PASSWORD,
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("Password changed successfully", response.data["detail"])
        self.user.refresh_from_db()
        other_user.refresh_from_db()
        self.assertTrue(self.user.check_password(VALID_PASSWORD))
        self.assertTrue(other_user.check_password("Original!Password94"))
        self.assertTrue(BlacklistedToken.objects.filter(token=outstanding).exists())

    def test_change_password_rejects_mismatched_confirmation(self):
        self.client.force_authenticate(self.user)
        response = self.client.post(
            self.url,
            {
                "current_password": "Original!Password94",
                "new_password": VALID_PASSWORD,
                "confirm_password": "Different!Password953",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("confirm_password", response.data)

    def test_change_password_applies_django_password_validators(self):
        self.client.force_authenticate(self.user)
        response = self.client.post(
            self.url,
            {
                "current_password": "Original!Password94",
                "new_password": "12345678",
                "confirm_password": "12345678",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("new_password", response.data)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("Original!Password94"))
