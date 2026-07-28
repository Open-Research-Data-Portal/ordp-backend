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