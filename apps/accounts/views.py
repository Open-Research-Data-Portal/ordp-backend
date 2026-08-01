from django.shortcuts import render

from django.contrib.auth import get_user_model
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework.permissions import IsAuthenticated
from rest_framework_simplejwt.tokens import RefreshToken as RefreshTokenObj
from rest_framework_simplejwt.exceptions import TokenError
from .serializers import LoginSerializer, LogoutSerializer, ProfileSerializer
from rest_framework_simplejwt.views import TokenRefreshView
from .models import LoginSecurity
from .serializers import LoginSerializer

from django.core.mail import send_mail
from django.conf import settings
from django.utils.http import urlsafe_base64_encode, urlsafe_base64_decode
from django.utils.encoding import force_bytes, force_str

from .tokens import email_verification_token
from .serializers import LoginSerializer, LogoutSerializer, ProfileSerializer, RegisterSerializer
from .models import LoginSecurity, UserProfile

from django.contrib.auth.tokens import PasswordResetTokenGenerator
from .serializers import PasswordResetRequestSerializer, PasswordResetConfirmSerializer
from rest_framework_simplejwt.token_blacklist.models import OutstandingToken, BlacklistedToken


User = get_user_model()


def log_activity(user, action, target_object, ip_address, extra=None):
    """
    STUB — swap for Elsa's real logging utility once it exists.
    """
    print(f"ACTIVITY user={getattr(user, 'id', 'anonymous')} action={action} target={target_object} ip={ip_address} extra={extra or {}}")


def get_client_ip(request):
    x_forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR")
    if x_forwarded_for:
        return x_forwarded_for.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "unknown")


class LoginView(APIView):
    permission_classes = []

    def post(self, request):
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email = serializer.validated_data["email"]
        password = serializer.validated_data["password"]
        ip = get_client_ip(request)

        generic_error = Response(
            {"error": {"code": "INVALID_CREDENTIALS", "message": "Invalid email or password.", "field": None}},
            status=status.HTTP_401_UNAUTHORIZED,
        )

        try:
            user = User.objects.get(email=email)
        except User.DoesNotExist:
            log_activity(user=None, action="login_failure", target_object=email, ip_address=ip, extra={"reason": "no_account"})
            return generic_error

        security, _ = LoginSecurity.objects.get_or_create(user=user)

        if security.is_locked():
            log_activity(user=user, action="login_failure", target_object=str(user.id), ip_address=ip, extra={"reason": "locked"})
            return Response(
                {"error": {
                    "code": "ACCOUNT_LOCKED",
                    "message": f"Account locked until {security.locked_until.isoformat()}.",
                    "field": None,
                }},
                status=status.HTTP_403_FORBIDDEN,
            )

        if not user.check_password(password):
            security.register_failed_attempt()
            log_activity(user=user, action="login_failure", target_object=str(user.id), ip_address=ip, extra={"reason": "wrong_password"})
            if security.is_locked():
                log_activity(user=user, action="account_locked", target_object=str(user.id), ip_address=ip)
            return generic_error

        if not user.is_active:
            log_activity(user=user, action="login_failure", target_object=str(user.id), ip_address=ip, extra={"reason": "unverified"})
            return Response(
                {"error": {"code": "EMAIL_NOT_VERIFIED", "message": "Please verify your email before logging in.", "field": None}},
                status=status.HTTP_403_FORBIDDEN,
            )

        security.reset()

        security.reset()
        log_activity(user=user, action="login_success", target_object=str(user.id), ip_address=ip)

        refresh = RefreshToken.for_user(user)

        return Response({
            "access": str(refresh.access_token),
            "refresh": str(refresh),
            "user": {"id": user.id, "email": user.email},
        }, status=status.HTTP_200_OK)
class LogoutView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = LogoutSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        refresh_token_str = serializer.validated_data["refresh"]
        ip = get_client_ip(request)

        try:
            token = RefreshTokenObj(refresh_token_str)
            token.blacklist()
        except TokenError:
            return Response(
                {"error": {"code": "INVALID_TOKEN", "message": "Refresh token is invalid or already blacklisted.", "field": None}},
                status=status.HTTP_400_BAD_REQUEST,
            )

        log_activity(user=request.user, action="logout", target_object=str(request.user.id), ip_address=ip)

        return Response({"detail": "Successfully logged out."}, status=status.HTTP_200_OK)
class ProfileView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        serializer = ProfileSerializer(request.user)
        return Response(serializer.data, status=status.HTTP_200_OK)

    def patch(self, request):
        serializer = ProfileSerializer(request.user, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()

        log_activity(
            user=request.user,
            action="profile_updated",
            target_object=str(request.user.id),
            ip_address=get_client_ip(request),
        )

        return Response(serializer.data, status=status.HTTP_200_OK)
class CustomTokenRefreshView(TokenRefreshView):
    def post(self, request, *args, **kwargs):
        refresh_str = request.data.get("refresh")
        user_id = None
        if refresh_str:
            try:
                token = RefreshTokenObj(refresh_str)
                user_id = token.get("user_id")
            except TokenError:
                pass  # invalid token — let the real view below handle the actual error response

        response = super().post(request, *args, **kwargs)

        if response.status_code == 200:
            log_activity(
                user=None,
                action="token_refresh",
                target_object=str(user_id) if user_id else "unknown",
                ip_address=get_client_ip(request),
            )

        return response




class RegisterView(APIView):
    permission_classes = []

    def post(self, request):
        serializer = RegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        ip = get_client_ip(request)

        user = User.objects.create_user(
            username=data["username"],
            email=data["email"],
            password=data["password"],
            is_active=False,
        )
        UserProfile.objects.create(user=user, full_name=data["full_name"])

        uid = urlsafe_base64_encode(force_bytes(user.pk))
        token = email_verification_token.make_token(user)
        verify_link = f"{settings.FRONTEND_URL}/verify-email?uid={uid}&token={token}"

        send_mail(
            subject="Verify your ORDP account",
            message=f"Welcome to ORDP. Verify your email by visiting: {verify_link}\n\nThis link expires soon — if it does, request a new one.",
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[user.email],
        )

        log_activity(user=user, action="registration_requested", target_object=str(user.id), ip_address=ip)

        return Response(
            {"detail": "Registration successful. Check your university email to verify your account."},
            status=status.HTTP_201_CREATED,
        )


class VerifyEmailView(APIView):
    permission_classes = []

    def post(self, request):
        uid = request.data.get("uid")
        token = request.data.get("token")
        ip = get_client_ip(request)

        try:
            user_id = force_str(urlsafe_base64_decode(uid))
            user = User.objects.get(pk=user_id)
        except (TypeError, ValueError, OverflowError, User.DoesNotExist):
            return Response(
                {"error": {"code": "INVALID_LINK", "message": "This verification link is invalid.", "field": None}},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not email_verification_token.check_token(user, token):
            return Response(
                {"error": {"code": "EXPIRED_OR_INVALID_TOKEN", "message": "This verification link is invalid or has expired.", "field": None}},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user.is_active = True
        user.save()
        log_activity(user=user, action="email_verified", target_object=str(user.id), ip_address=ip)

        return Response({"detail": "Email verified. You can now log in."}, status=status.HTTP_200_OK)
password_reset_token = PasswordResetTokenGenerator()




class PasswordResetRequestView(APIView):
    permission_classes = []

    def post(self, request):
        serializer = PasswordResetRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email = serializer.validated_data["email"]
        ip = get_client_ip(request)

        try:
            user = User.objects.get(email__iexact=email)
        except User.DoesNotExist:
            # Same email either way — don't reveal whether the account exists.
            return Response(
                {"detail": "If an account exists with that email, a reset link has been sent."},
                status=status.HTTP_200_OK,
            )

        uid = urlsafe_base64_encode(force_bytes(user.pk))
        token = password_reset_token.make_token(user)
        reset_link = f"{settings.FRONTEND_URL}/reset-password?uid={uid}&token={token}"

        send_mail(
            subject="Reset your ORDP password",
            message=f"Reset your password by visiting: {reset_link}\n\nIf you didn't request this, ignore this email.",
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[user.email],
        )

        log_activity(user=user, action="password_reset_requested", target_object=str(user.id), ip_address=ip)

        return Response(
            {"detail": "If an account exists with that email, a reset link has been sent."},
            status=status.HTTP_200_OK,
        )


class PasswordResetConfirmView(APIView):
    permission_classes = []

    def post(self, request):
        serializer = PasswordResetConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        ip = get_client_ip(request)

        try:
            user_id = force_str(urlsafe_base64_decode(data["uid"]))
            user = User.objects.get(pk=user_id)
        except (TypeError, ValueError, OverflowError, User.DoesNotExist):
            return Response(
                {"error": {"code": "INVALID_LINK", "message": "This reset link is invalid.", "field": None}},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not password_reset_token.check_token(user, data["token"]):
            return Response(
                {"error": {"code": "EXPIRED_OR_INVALID_TOKEN", "message": "This reset link is invalid or has expired.", "field": None}},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user.set_password(data["new_password"])
        user.save()

        for token_obj in OutstandingToken.objects.filter(user=user):
            BlacklistedToken.objects.get_or_create(token=token_obj)

        log_activity(user=user, action="password_reset_completed", target_object=str(user.id), ip_address=ip)

        return Response({"detail": "Password reset successful. Please log in with your new password."}, status=status.HTTP_200_OK)