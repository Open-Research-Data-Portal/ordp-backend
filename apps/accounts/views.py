from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db.models import Q
from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.utils.http import urlsafe_base64_encode, urlsafe_base64_decode
from django.utils.encoding import force_bytes, force_str
from django.contrib.auth.tokens import PasswordResetTokenGenerator
from .serializers import ExtendedProfileSerializer  
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.tokens import RefreshToken as RefreshTokenObj
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.views import TokenRefreshView
from rest_framework_simplejwt.token_blacklist.models import OutstandingToken, BlacklistedToken
from django.db import transaction
from rest_framework.decorators import api_view, permission_classes
from apps.datasets.models import Contributor
from apps.notifications.models import Notification
from apps.notifications.services import notify
from django.utils import timezone
from .models import LoginSecurity, UserProfile, ActivityLog, EmailVerificationToken
from .serializers import (
    LoginSerializer, LogoutSerializer, ProfileSerializer, RegisterSerializer,
    PasswordResetRequestSerializer, PasswordResetConfirmSerializer,
)

User = get_user_model()
password_reset_token = PasswordResetTokenGenerator()

@api_view(["POST"]) 
@permission_classes([IsAuthenticated]) 
def add_other_interest(request):
    from apps.metadata.services import get_or_create_pending_category
    name = (request.data.get("name") or "").strip()
    if not name:
        return Response({"detail": "name is required."}, status=400)
    category = get_or_create_pending_category(name, request.user)
    request.user.profile.expertise.add(category)
    return Response({
        "status": "added", "category_id": category.id,
        "pending_review": category.status == category.Status.PENDING,
    }, status=201)

def log_activity(user, action, target_object, ip_address, extra=None):
    ActivityLog.objects.create(
        user=user,
        action=action,
        target_object=target_object,
        ip_address=ip_address,
        extra=extra or {},
    )


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
        identifier = serializer.validated_data["identifier"]
        password = serializer.validated_data["password"]
        ip = get_client_ip(request)

        generic_error = Response(
            {"error": {"code": "INVALID_CREDENTIALS", "message": "Invalid email/username or password.", "field": None}},
            status=status.HTTP_401_UNAUTHORIZED,
        )

        try:
            user = User.objects.get(Q(email__iexact=identifier) | Q(username__iexact=identifier))
        except User.DoesNotExist:
            log_activity(user=None, action="login_failure", target_object=identifier, ip_address=ip, extra={"reason": "no_account"})
            return generic_error
        except User.MultipleObjectsReturned:
            log_activity(user=None, action="login_failure", target_object=identifier, ip_address=ip, extra={"reason": "ambiguous_identifier"})
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
        log_activity(user=user, action="login_success", target_object=str(user.id), ip_address=ip)

        stay_logged_in = serializer.validated_data.get("stay_logged_in", False)
        refresh = RefreshToken.for_user(user)

        if stay_logged_in:
            refresh.set_exp(lifetime=timedelta(days=30))

        return Response({
            "access": str(refresh.access_token),
            "refresh": str(refresh),
            "user": {"id": user.id, "email": user.email},
            "stay_logged_in": stay_logged_in,
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




class CompleteProfileView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        data = ExtendedProfileSerializer(request.user.profile).data
        data["role"] = request.user.profile.role
        data["roles"] = list(request.user.profile.roles.values_list("role", flat=True))
        return Response(data)

    def patch(self, request):
        old_full_name = request.user.profile.full_name
        serializer = ExtendedProfileSerializer(request.user.profile, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        new_full_name = request.user.profile.full_name
        if new_full_name != old_full_name:
            log_activity(
            user=request.user, action="full_name_changed",
            target_object=str(request.user.id), ip_address=get_client_ip(request),
            extra={"old": old_full_name, "new": new_full_name},
        )
        log_activity(
            user=request.user, action="profile_completed",
            target_object=str(request.user.id), ip_address=get_client_ip(request),
        )
        return Response(serializer.data)

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

        with transaction.atomic():
            user = User.objects.create_user(
                username=data["username"],
                email=data["email"],
                password=data["password"],
                is_active=False,
            )
            UserProfile.objects.create(user=user, full_name=data["full_name"])

        
        verification = EmailVerificationToken.objects.create(
            user=user,
            expires_at=timezone.now() + timedelta(hours=24),
        )
        verify_link = f"{settings.FRONTEND_URL}/verify-email?token={verification.token}"
        try:
            html_content = render_to_string("accounts/emails/verify_email.html", {
                "full_name": data["full_name"],
                "verify_link": verify_link,
            })
            text_content = f"Welcome to ORDP. Verify your email by visiting: {verify_link}"

            email = EmailMultiAlternatives(
                subject="Verify your ORDP account",
                body=text_content,
                from_email=settings.DEFAULT_FROM_EMAIL,
                to=[user.email],
            )
            email.attach_alternative(html_content, "text/html")
            email.send()
        except Exception:
            import logging
            logging.getLogger(__name__).exception("Failed to send verification email to %s", user.email)

        log_activity(user=user, action="registration_requested", target_object=str(user.id), ip_address=ip)

        return Response(
            {"detail": "Registration successful. Check your university email to verify your account."},
            status=status.HTTP_201_CREATED,
        )


class VerifyEmailView(APIView):
    permission_classes = []

    def post(self, request):
        token_str = request.data.get("token")
        ip = get_client_ip(request)

        if not token_str:
            return Response(
                {"error": {"code": "INVALID_LINK", "message": "This verification link is invalid.", "field": None}},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            verification = EmailVerificationToken.objects.select_related("user").get(token=token_str)
        except (EmailVerificationToken.DoesNotExist, ValueError):
            log_activity(user=None, action="email_verification_failure", target_object=str(token_str), ip_address=ip, extra={"reason": "invalid_link"})
            return Response(
                {"error": {"code": "INVALID_LINK", "message": "This verification link is invalid.", "field": None}},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not verification.is_valid():
            log_activity(user=verification.user, action="email_verification_failure", target_object=str(verification.user.id), ip_address=ip, extra={"reason": "expired_or_used"})
            return Response(
                {"error": {"code": "EXPIRED_OR_INVALID_TOKEN", "message": "This verification link is invalid or has expired.", "field": None}},
                status=status.HTTP_400_BAD_REQUEST,
            )

        verification.is_used = True
        verification.save(update_fields=["is_used"])
        user = verification.user
        user.is_active = True
        user.save(update_fields=["is_active"])
        log_activity(user=user, action="email_verified", target_object=str(user.id), ip_address=ip)

        return Response({"detail": "Email verified. You can now log in."}, status=status.HTTP_200_OK)


class PasswordResetRequestView(APIView):
    permission_classes = []

    def post(self, request):
        serializer = PasswordResetRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        email_addr = serializer.validated_data["email"]
        ip = get_client_ip(request)

        try:
            user = User.objects.get(email__iexact=email_addr)
        except User.DoesNotExist:
            return Response(
                {"detail": "If an account exists with that email, a reset link has been sent."},
                status=status.HTTP_200_OK,
            )

        uid = urlsafe_base64_encode(force_bytes(user.pk))
        token = password_reset_token.make_token(user)
        reset_link = f"{settings.FRONTEND_URL}/reset-password?uid={uid}&token={token}"

        html_content = render_to_string("accounts/emails/password_reset_email.html", {
            "reset_link": reset_link,
        })
        text_content = f"Reset your password by visiting: {reset_link}"

        email = EmailMultiAlternatives(
            subject="Reset your ORDP password",
            body=text_content,
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[user.email],
        )
        email.attach_alternative(html_content, "text/html")
        email.send()

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


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def add_other_interest(request):
    from apps.metadata.services import get_or_create_pending_category
    name = (request.data.get("name") or "").strip()
    if not name:
        return Response({"detail": "name is required."}, status=400)
    category = get_or_create_pending_category(name, request.user)
    request.user.profile.expertise.add(category)
    return Response({
        "status": "added", "category_id": category.id,
        "pending_review": category.status == category.Status.PENDING,
    }, status=201)



@api_view(["GET"])
@permission_classes([IsAuthenticated])
def search_users(request):
    query = request.query_params.get("q", "").strip()
    if not query:
        return Response([])
    qs = User.objects.filter(
        Q(profile__full_name__icontains=query) | Q(email__icontains=query)
    ).select_related("profile")[:10]
    return Response([
        {"id": u.id, "full_name": u.profile.full_name, "email": u.email}
        for u in qs if hasattr(u, "profile")
    ])