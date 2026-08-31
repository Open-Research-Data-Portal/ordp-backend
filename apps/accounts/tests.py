from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.urls import reverse
from django.utils import timezone

from rest_framework import status
from rest_framework.test import APITestCase

from .models import EmailVerificationToken


User = get_user_model()


class ResendVerificationEmailThrottleTests(APITestCase):

    def setUp(self):
        cache.clear()

        self.user = User.objects.create_user(
            username="testuser",
            email="testuser@aastu.edu.et",
            password="StrongPassword123!",
            is_active=False,
        )

        self.url = reverse("resend-verification")

    @patch("apps.accounts.views.EmailMultiAlternatives.send")
    def test_first_resend_is_allowed(self, mock_send):
        response = self.client.post(
            self.url,
            {"email": "testuser@aastu.edu.et"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        mock_send.assert_called_once()

        self.assertTrue(
            EmailVerificationToken.objects.filter(
                user=self.user
            ).exists()
        )

    @patch("apps.accounts.views.EmailMultiAlternatives.send")
    def test_second_resend_within_60_seconds_is_blocked(self, mock_send):
        first_response = self.client.post(
            self.url,
            {"email": "testuser@aastu.edu.et"},
            format="json",
        )

        self.assertEqual(
            first_response.status_code,
            status.HTTP_200_OK,
        )

        second_response = self.client.post(
            self.url,
            {"email": "testuser@aastu.edu.et"},
            format="json",
        )

        self.assertEqual(
            second_response.status_code,
            status.HTTP_429_TOO_MANY_REQUESTS,
        )

        self.assertEqual(mock_send.call_count, 1)

    @patch("apps.accounts.views.EmailMultiAlternatives.send")
    def test_different_email_has_separate_email_throttle(self, mock_send):
        User.objects.create_user(
            username="testuser2",
            email="testuser2@aastu.edu.et",
            password="StrongPassword123!",
            is_active=False,
        )

        first_response = self.client.post(
            self.url,
            {"email": "testuser@aastu.edu.et"},
            format="json",
        )

        second_response = self.client.post(
            self.url,
            {"email": "testuser2@aastu.edu.et"},
            format="json",
        )

        self.assertEqual(
            first_response.status_code,
            status.HTTP_200_OK,
        )

        self.assertEqual(
            second_response.status_code,
            status.HTTP_200_OK,
        )

        self.assertEqual(mock_send.call_count, 2)

    @patch("apps.accounts.views.EmailMultiAlternatives.send")
    def test_sixth_request_from_same_ip_is_blocked(self, mock_send):
        emails = [
            f"test{i}@aastu.edu.et"
            for i in range(1, 7)
        ]

        for i, email in enumerate(emails):
            User.objects.create_user(
                username=f"testuser{i}",
                email=email,
                password="StrongPassword123!",
                is_active=False,
            )

        responses = []

        for email in emails:
            response = self.client.post(
                self.url,
                {"email": email},
                format="json",
            )
            responses.append(response)

        for response in responses[:5]:
            self.assertEqual(
                response.status_code,
                status.HTTP_200_OK,
            )

        self.assertEqual(
            responses[5].status_code,
            status.HTTP_429_TOO_MANY_REQUESTS,
        )

        self.assertEqual(mock_send.call_count, 5)

    @patch("apps.accounts.views.EmailMultiAlternatives.send")
    def test_verified_account_cannot_resend(self, mock_send):
        self.user.is_active = True
        self.user.save(update_fields=["is_active"])

        response = self.client.post(
            self.url,
            {"email": "testuser@aastu.edu.et"},
            format="json",
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_400_BAD_REQUEST,
        )

        mock_send.assert_not_called()

    def test_missing_email_is_rejected(self):
        response = self.client.post(
            self.url,
            {},
            format="json",
        )

        self.assertEqual(
            response.status_code,
            status.HTTP_400_BAD_REQUEST,
        )