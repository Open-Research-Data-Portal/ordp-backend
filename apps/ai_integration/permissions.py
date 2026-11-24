import hmac
from django.conf import settings
from rest_framework.permissions import BasePermission
from rest_framework.exceptions import AuthenticationFailed


class IsWorkerAuthenticated(BasePermission):
    """Authenticate internal worker using ORDP_WORKER_TOKEN Bearer token.

    Never accepts standard user credentials or JWT tokens.
    Denies all requests with 401 if ORDP_WORKER_TOKEN is unset or set to the default placeholder.
    Never accepts an empty Bearer value.
    """
    message = "Invalid or missing worker token."

    def has_permission(self, request, view):
        expected_token = getattr(settings, "ORDP_WORKER_TOKEN", "").strip()
        if not expected_token or expected_token == "changeme-worker-token":
            raise AuthenticationFailed({"detail": self.message, "code": "UNAUTHORIZED"})

        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            raise AuthenticationFailed({"detail": self.message, "code": "UNAUTHORIZED"})

        provided_token = auth_header[len("Bearer "):].strip()
        if not provided_token or not hmac.compare_digest(provided_token, expected_token):
            raise AuthenticationFailed({"detail": self.message, "code": "UNAUTHORIZED"})

        return True
