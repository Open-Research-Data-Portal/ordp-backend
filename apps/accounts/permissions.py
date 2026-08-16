from rest_framework.permissions import BasePermission
from .models import UserProfile


class HasRole(BasePermission):
    allowed_roles = []

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            self.message = "You must be logged in to do this."
            return False
        try:
            profile = UserProfile.objects.get(user=request.user)
        except UserProfile.DoesNotExist:
            self.message = "No profile is associated with this account. Please contact support."
            return False
        request.user_profile = profile
        if not profile.has_role(*self.allowed_roles):
            role_names = " or ".join(role.replace("_", " ").title() for role in self.allowed_roles)
            self.message = f"You must be a {role_names} to do this."
            return False
        return True


class IsResearcherOrAdmin(HasRole):
    allowed_roles = ["researcher", "admin"]


class IsAdminOnly(HasRole):
    allowed_roles = ["admin"]


class IsCheckerOrAdmin(HasRole):
    allowed_roles = ["checker", "admin"]


class IsResearcherOnly(HasRole):
    allowed_roles = ["researcher"]

    def has_permission(self, request, view):
        result = super().has_permission(request, view)
        if not result and request.user and request.user.is_authenticated and hasattr(request.user, "profile"):
            self.message = "Complete your profile to start uploading."
        return result