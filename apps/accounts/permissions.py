from rest_framework.permissions import BasePermission
from .models import UserProfile


class HasRole(BasePermission):
    allowed_roles = []

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False

        profile = getattr(request.user, "profile", None)

        if not profile:
            return False

        return profile.has_role(*self.allowed_roles)
class CanUploadDatasets(BasePermission):
    message = "You do not have permission to upload datasets."

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            self.message = "You must be logged in to upload datasets."
            return False

        profile = getattr(request.user, "profile", None)

        if not profile:
            self.message = "No profile is associated with this account."
            return False

        if not profile.is_profile_complete():
            self.message = "Complete your profile before uploading datasets."
            return False

        if not profile.can_upload_datasets:
            self.message = "You do not currently have permission to upload datasets."
            return False

        return True
class IsAdminOnly(HasRole):
    allowed_roles = ["admin"]


class IsReviewerOrAdmin(HasRole):
    allowed_roles = ["reviewer", "admin"]


