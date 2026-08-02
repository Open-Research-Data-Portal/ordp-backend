from rest_framework.permissions import BasePermission
from .models import UserProfile

class HasRole(BasePermission):
    allowed_roles = []

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        try:
            profile = UserProfile.objects.get(user=request.user)
        except UserProfile.DoesNotExist:
            return False
        request.user_profile = profile 
        return profile.role in self.allowed_roles



class IsResearcherOrAdmin(HasRole):
    allowed_roles = ["researcher", "admin"]

    def has_permission(self, request, view):
        if not super().has_permission(request, view):
            return False
        profile = request.user_profile
        if profile.role == "researcher" and not profile.terms_accepted:
            return False
        return True


class IsAdminOnly(HasRole):
    allowed_roles = ["admin"]


class IsCheckerOrAdmin(HasRole):
    allowed_roles = ["checker", "admin"]

class IsResearcherOnly(HasRole):
    allowed_roles = ["researcher"]

