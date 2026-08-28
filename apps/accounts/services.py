from requests import Response

from apps.accounts.permissions import IsAdminOnly

from .models import UserRole
from apps.notifications.services import notify
from apps.notifications.models import Notification
from rest_framework.decorators import api_view, permission_classes
@api_view(["POST"])
@permission_classes([IsAdminOnly])
def admin_grant_upload_access(request, user_id):
    target_user = get_object_or_404(User, id=user_id)
    profile = target_user.profile

    if not profile.is_profile_complete():
        return Response(
            {"detail": "User must complete their profile first."},
            status=400,
        )

    profile.can_upload_datasets = True
    profile.save(update_fields=["can_upload_datasets"])

    return Response({
        "status": "granted",
        "can_upload_datasets": True,
    })


@api_view(["POST"])
@permission_classes([IsAdminOnly])
def admin_revoke_upload_access(request, user_id):
    target_user = get_object_or_404(User, id=user_id)
    profile = target_user.profile

    profile.can_upload_datasets = False
    profile.save(update_fields=["can_upload_datasets"])

    return Response({
        "status": "revoked",
        "can_upload_datasets": False,
    })