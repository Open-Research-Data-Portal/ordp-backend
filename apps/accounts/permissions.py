from django.core.exceptions import ObjectDoesNotExist
from rest_framework.permissions import BasePermission

from apps.accounts.models import UserRole
from apps.access_control.services import has_permission


class HasRole(BasePermission):
    """Legacy role gate. Prefer HasPlatformPermission after V2 cutover."""
    allowed_roles = []

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        try:
            profile = getattr(request.user, "profile", None)
        except ObjectDoesNotExist:
            return False
        if not profile:
            return False
        return profile.has_role(*self.allowed_roles)


class HasPlatformPermission(BasePermission):
    """Require a V2 platform permission code."""
    code = None
    message = "You do not have permission to perform this action."

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        code = self.code or getattr(view, "required_permission", None)
        if not code:
            return False
        return has_permission(request.user, code)


class HasAnyPlatformPermission(BasePermission):
    codes = []
    message = "You do not have permission to perform this action."

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        codes = self.codes or getattr(view, "required_permissions", [])
        return any(has_permission(request.user, code) for code in codes)


class CanUploadDatasets(BasePermission):
    message = "You do not have permission to upload datasets."

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            self.message = "You must be logged in to upload datasets."
            return False
        try:
            profile = getattr(request.user, "profile", None)
        except ObjectDoesNotExist:
            self.message = "No profile is associated with this account."
            return False
        if not profile:
            self.message = "No profile is associated with this account."
            return False
        if not profile.is_profile_complete():
            self.message = "Complete your profile before uploading datasets."
            return False
        if profile.can_upload_datasets or has_permission(request.user, "dataset.create"):
            return True
        self.message = "You do not currently have permission to upload datasets."
        return False


class IsAdminOnly(HasPlatformPermission):
    """V2 cutover: platform user managers replace legacy admin role gates."""
    code = "user.manage"
    message = "Admin permission required."

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        if request.user.is_superuser or request.user.is_staff:
            return True
        if UserRole.objects.filter(
            profile__user=request.user, role=UserRole.RoleChoice.ADMIN
        ).exists():
            return True
        return super().has_permission(request, view)


class IsReviewerOrAdmin(HasAnyPlatformPermission):
    codes = ["dataset.approve", "dataset.publish", "access_request.review", "user.manage"]
    message = "Reviewer or admin permission required."


class IsReviewerOnly(HasAnyPlatformPermission):
    """Committee review authority without requiring user.manage."""
    codes = ["dataset.approve", "access_request.review", "access_request.approve"]
    message = "Reviewer permission required."
