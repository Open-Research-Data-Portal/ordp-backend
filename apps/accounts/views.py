from datetime import timedelta
from .serializers import ExtendedProfileSerializer, PublicProfileSerializer
from django.contrib.auth import get_user_model
from django.db.models import Q
from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
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
from apps.datasets.services.retry_assignment import retry_pending_assignments
from django.shortcuts import get_object_or_404
from .models import (
    LoginSecurity,
    UserProfile,
    ActivityLog,
    EmailVerificationToken,
    PasswordResetToken,
    College,
    CenterOfExcellence,
    UserRole,
)
from .serializers import LoginSerializer, LogoutSerializer, ProfileSerializer, RegisterSerializer,PasswordResetRequestSerializer, PasswordResetConfirmSerializer
from apps.accounts.permissions import IsAdminOnly
from .throttles import (
    VerificationEmailRateThrottle,
    VerificationEmailIPRateThrottle,
)
User = get_user_model()

@api_view(["POST"])
@permission_classes([IsAuthenticated])
def add_other_interest(request):
    from apps.metadata.services import get_or_create_category_from_interest_other

    name = (request.data.get("name") or "").strip()

    if not name:
        return Response(
            {"detail": "name is required."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    category = get_or_create_category_from_interest_other(name, request.user)

    request.user.profile.interests.add(category)

    return Response(
        {
            "status": "added",
            "category_id": category.id,
        },
        status=status.HTTP_201_CREATED,
    )


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
            "user": {
                "id": user.id,
                "email": user.email,
                "must_change_password": getattr(
                    getattr(user, "profile", None), "must_change_password", False
                ),
            },
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
        roles = list(
    request.user.profile.roles.values_list("role", flat=True)
)

        data["roles"] = roles
        data["role"] = roles[0] if roles else None
        return Response(data)

    def patch(self, request):
        profile = request.user.profile

        old_full_name = profile.full_name
        profile_was_complete = profile.is_profile_complete()

        serializer = ExtendedProfileSerializer(
            profile,
            data=request.data,
            partial=True,
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()

        profile.refresh_from_db()

        profile_is_complete = profile.is_profile_complete()
        new_full_name = profile.full_name

        # Automatically grant upload permission only when the profile
        # becomes complete, unless an admin has explicitly revoked it.
        if (
            not profile_was_complete
            and profile_is_complete
            and not profile.upload_permission_revoked
        ):
            profile.can_upload_datasets = True
            profile.save(update_fields=["can_upload_datasets"])

        if new_full_name != old_full_name:
            log_activity(
                user=request.user,
                action="full_name_changed",
                target_object=str(request.user.id),
                ip_address=get_client_ip(request),
                extra={
                    "old": old_full_name,
                    "new": new_full_name,
                },
            )

        if not profile_was_complete and profile_is_complete:
            log_activity(
                user=request.user,
                action="profile_completed",
                target_object=str(request.user.id),
                ip_address=get_client_ip(request),
            )

        return Response(
            ExtendedProfileSerializer(profile).data,
            status=status.HTTP_200_OK,
        )

class CustomTokenRefreshView(TokenRefreshView):
    def post(self, request, *args, **kwargs):
        refresh_str = request.data.get("refresh")
        user_id = None
        if refresh_str:
            try:
                token = RefreshTokenObj(refresh_str)
                user_id = token.get("user_id")
            except TokenError:
                pass 

        response = super().post(request, *args, **kwargs)

        if response.status_code == 200:
            log_activity(
                user=None,
                action="token_refresh",
                target_object=str(user_id) if user_id else "unknown",
                ip_address=get_client_ip(request),
            )
        else:
            log_activity(
                user=None,
                action="token_refresh_failure",
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
            user.profile.full_name = data["full_name"]
            user.profile.save(update_fields=["full_name"])

        
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
class ResendVerificationEmailView(APIView):
    permission_classes = []
    throttle_classes = [
        VerificationEmailRateThrottle,
        VerificationEmailIPRateThrottle,
    ]
    def post(self, request):
        email_addr = request.data.get("email", "").strip().lower()
        ip = get_client_ip(request)

        if not email_addr:
            return Response(
                {
                    "error": {
                        "code": "INVALID_EMAIL",
                        "message": "Email is required.",
                        "field": "email",
                    }
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            user = User.objects.select_related("profile").get(
                email__iexact=email_addr
            )
        except User.DoesNotExist:
            # Do not reveal whether the email exists.
            log_activity(
                user=None,
                action="verification_resend_requested_unknown_email",
                target_object=email_addr,
                ip_address=ip,
            )

            return Response(
                {
                    "detail": (
                        "If an unverified account exists with that email, "
                        "a new verification link has been sent."
                    )
                },
                status=status.HTTP_200_OK,
            )

        # Already verified/active
        if user.is_active:
            log_activity(
                user=user,
                action="verification_resend_requested_verified",
                target_object=str(user.id),
                ip_address=ip,
            )

            return Response(
                {
                    "detail": "This account has already been verified."
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Invalidate all previous unused verification tokens.
        EmailVerificationToken.objects.filter(
            user=user,
            is_used=False,
        ).update(is_used=True)

        # Create a fresh 24-hour verification token.
        verification = EmailVerificationToken.objects.create(
            user=user,
            expires_at=timezone.now() + timedelta(hours=24),
        )

        verify_link = (
            f"{settings.FRONTEND_URL}"
            f"/verify-email?token={verification.token}"
        )

        try:
            full_name = (
                user.profile.full_name
                if hasattr(user, "profile")
                else user.username
            )

            html_content = render_to_string(
                "accounts/emails/verify_email.html",
                {
                    "full_name": full_name,
                    "verify_link": verify_link,
                },
            )

            text_content = (
                f"Verify your ORDP account by visiting: {verify_link}"
            )

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

            logging.getLogger(__name__).exception(
                "Failed to resend verification email to %s",
                user.email,
            )

            return Response(
                {
                    "error": {
                        "code": "EMAIL_SEND_FAILED",
                        "message": "Unable to send the verification email. Please try again later.",
                        "field": None,
                    }
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        log_activity(
            user=user,
            action="verification_email_resent",
            target_object=str(user.id),
            ip_address=ip,
        )

        return Response(
            {
                "detail": (
                    "A new verification link has been sent to your "
                    "university email."
                )
            },
            status=status.HTTP_200_OK,
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
                {"error": {"code": "INVALID_LINK","message": "This verification link is invalid.", "field": None}},
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

        log_activity(
            user=user,
            action="email_verified",
            target_object=str(user.id),
            ip_address=ip,
        )

        # Automatically log the user in after successful email verification
        refresh = RefreshToken.for_user(user)

        return Response(
            {
                "detail": "Email verified successfully.",
                "access": str(refresh.access_token),
                "refresh": str(refresh),
                "user": {
                    "id": user.id,
                    "email": user.email,
                },
                "stay_logged_in": False,
            },
            status=status.HTTP_200_OK,
        )


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
            log_activity(user=None, action="password_reset_requested_unknown_email", target_object=email_addr, ip_address=ip, extra={"reason": "no_account"})
            return Response(
                {"detail": "If an account exists with that email, a reset link has been sent."},
                status=status.HTTP_200_OK,
            )


                # Invalidate any previous unused reset tokens for this user
        PasswordResetToken.objects.filter(user=user, is_used=False).update(is_used=True)
        reset_token = PasswordResetToken.objects.create(
            user=user,
            expires_at=timezone.now() + timedelta(hours=24),
        )
        reset_link = f"{settings.FRONTEND_URL}/reset-password?token={reset_token.token}"

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

        token_str = data.get("token")
        if not token_str:
            return Response(
                {"error": {"code": "INVALID_LINK", "message": "This reset link is invalid.", "field": None}},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:

            reset_token = PasswordResetToken.objects.select_related("user").get(token=token_str)
        except (PasswordResetToken.DoesNotExist, ValueError):
            log_activity(user=None, action="password_reset_confirm_failure", target_object=str(token_str), ip_address=ip, extra={"reason": "invalid_link"})

            return Response(
                {"error": {"code": "INVALID_LINK","message": "This reset link is invalid.", "field":None}},
                status=status.HTTP_400_BAD_REQUEST,
            )


        if not reset_token.is_valid():
            log_activity(user=reset_token.user, action="password_reset_confirm_failure", target_object=str(reset_token.user.id), ip_address=ip, extra={"reason": "expired_or_used"})
            return Response(
                {"error": {"code": "EXPIRED_OR_INVALID_TOKEN", "message": "This reset link is invalidor has expired.", "field": None}},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user = reset_token.user
        user.set_password(data["new_password"])
        user.save()
        if hasattr(user, "profile") and user.profile.must_change_password:
            user.profile.must_change_password = False
            user.profile.save(update_fields=["must_change_password"])
        
        reset_token.is_used = True
        reset_token.save(update_fields=["is_used"])

        for token_obj in OutstandingToken.objects.filter(user=user):
            BlacklistedToken.objects.get_or_create(token=token_obj)

        log_activity(user=user, action="password_reset_completed", target_object=str(user.id), ip_address=ip)
        if user.profile.roles.filter(role=UserRole.RoleChoice.REVIEWER).exists():
            retry_pending_assignments()

        return Response({"detail": "Password reset successful. Please log in with your new password."}, status=status.HTTP_200_OK)


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




@api_view(["GET"])
def list_colleges(request):
    colleges = College.objects.order_by("name").values("id", "name")

    return Response({
        "results": list(colleges)
    })


@api_view(["GET"])
def list_centers_of_excellence(request):
    centers = CenterOfExcellence.objects.order_by("name").values("id", "name")

    return Response({
        "results": list(centers)
    })




class UserPublicProfileView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, user_id):
        user = get_object_or_404(
            User.objects.select_related("profile"),
            id=user_id,
        )

        profile = user.profile

        # Owner and admin can always view the profile
        if (
            user.id == request.user.id
            or request.user.profile.has_role("admin")
        ):
            serializer = ExtendedProfileSerializer(profile)
            return Response(serializer.data)

        # Other users can only view public profiles
        if profile.profile_visibility != "public":
            return Response(
                {
                    "detail": "This profile is private."
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        serializer = PublicProfileSerializer(profile)
        return Response(serializer.data)



class ProfileOptionsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response({
            "academia": [
                {"value": value, "label": label}
                for value, label in UserProfile.Academia.choices
            ],
            "academic_title": [
                {"value": value, "label": label}
                for value, label in UserProfile.AcademicTitle.choices
            ],
            "academic_rank": [
                {"value": value, "label": label}
                for value, label in UserProfile.AcademicRank.choices
            ],
            "highest_degree": [
                {"value": value, "label": label}
                for value, label in UserProfile.HighestDegree.choices
            ],
            "profile_visibility": [
                {"value": value, "label": label}
                for value, label in UserProfile.VISIBILITY_CHOICES
            ],
        })



class UpdateDatasetUploadPermissionView(APIView):
    permission_classes = [IsAuthenticated, IsAdminOnly]

    def patch(self, request, user_id):
        user = get_object_or_404(
            User.objects.select_related("profile"),
            id=user_id,
        )

        can_upload = request.data.get("can_upload_datasets")

        if not isinstance(can_upload, bool):
            return Response(
                {
                    "detail": "can_upload_datasets must be true or false."
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        profile = user.profile

        profile.can_upload_datasets = can_upload

        # If admin revokes permission, prevent automatic restoration.
        if not can_upload:
            profile.upload_permission_revoked = True

        # If admin grants permission again, allow automatic permission
        # logic to work again in the future.
        else:
            profile.upload_permission_revoked = False

        profile.save(
            update_fields=[
                "can_upload_datasets",
                "upload_permission_revoked",
            ]
        )

        log_activity(
            user=request.user,
            action=(
                "dataset_upload_permission_granted"
                if can_upload
                else "dataset_upload_permission_revoked"
            ),
            target_object=str(user.id),
            ip_address=get_client_ip(request),
            extra={
                "target_user_id": str(user.id),
                "can_upload_datasets": can_upload,
            },
        )

        return Response(
            {
                "user_id": str(user.id),
                "can_upload_datasets": profile.can_upload_datasets,
                "upload_permission_revoked": profile.upload_permission_revoked,
            },
            status=status.HTTP_200_OK,
        )