from rest_framework.permissions import BasePermission


class HasRole(BasePermission):
    """
    Base class — subclass this and set `allowed_roles` to restrict
    a view to specific roles. Usage in a view:

        class SomeView(APIView):
            permission_classes = [IsAuthenticated, IsResearcherOrAdmin]
    """
    allowed_roles = []

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        try:
            role = request.user.profile.role
        except AttributeError:
            return False
        return role in self.allowed_roles


class IsResearcherOrAdmin(HasRole):
    allowed_roles = ["researcher", "admin"]


class IsAdminOnly(HasRole):
    allowed_roles = ["admin"]


class IsCheckerOrAdmin(HasRole):
    allowed_roles = ["checker", "admin"]