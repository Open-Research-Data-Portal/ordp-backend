import hmac
from django.conf import settings
from rest_framework.permissions import BasePermission
from rest_framework.exceptions import AuthenticationFailed


class IsWorkerAuthenticated(BasePermission):
    """Authenticate internal worker using ORDP_WORKER_TOKEN Bearer token.

    Never accepts standard user credentials or JWT tokens.
    """
    message = "Invalid or missing worker token."

    def has_permission(self, request, view):
        expected_token = getattr(settings, "ORDP_WORKER_TOKEN", "")
        if not expected_token:
            return False

        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            raise AuthenticationFailed({"detail": self.message, "code": "UNAUTHORIZED"})

        provided_token = auth_header[len("Bearer "):].strip()
        if not hmac.compare_digest(provided_token, expected_token):
            raise AuthenticationFailed({"detail": self.message, "code": "UNAUTHORIZED"})

        return True
