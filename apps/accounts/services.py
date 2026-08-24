from .models import UserRole
from apps.notifications.services import notify
from apps.notifications.models import Notification


def submit_researcher_request(user):
    """
    Immediately grants the researcher role to a user.

    There is no admin approval step. Administrators are notified and
    can revoke the role later if necessary.
    """

    profile = user.profile

    # Require the user's profile to be complete first.
    if not profile.is_profile_complete():
        raise ValueError(
            "Please complete your profile before requesting researcher access."
        )

    # Create the researcher role immediately.
    role, created = UserRole.objects.get_or_create(
        profile=profile,
        role=UserRole.RoleChoice.RESEARCHER,
    )

    # Prevent duplicate requests/access.
    if not created:
        raise ValueError("You already have researcher access.")

    # Notify the user that access was granted immediately.
    notify(
        user=user,
        notification_type=Notification.NotificationType.RESEARCHER_APPROVED,
        message=(
            "Your researcher access has been granted. "
            "You can now upload datasets."
        ),
    )

    # Notify all administrators.
    admin_profiles = (
        profile.__class__.objects
        .filter(roles__role=UserRole.RoleChoice.ADMIN)
        .select_related("user")
        .distinct()
    )

    for admin_profile in admin_profiles:
        notify(
            user=admin_profile.user,
            notification_type=Notification.NotificationType.RESEARCHER_REQUEST,
            message=(
                f"{profile.full_name} has been granted researcher access."
            ),
        )

    return role


def revoke_researcher_access(user, revoked_by, reason=""):
    """
    Removes researcher access from a user.
    Intended to be called by an administrator.
    """

    reason = (reason or "").strip()

    if not reason:
        raise ValueError(
            "A reason is required to revoke researcher access."
        )

    profile = user.profile

    role = UserRole.objects.filter(
        profile=profile,
        role=UserRole.RoleChoice.RESEARCHER,
    ).first()

    if role is None:
        raise ValueError(
            "This user does not currently have researcher access."
        )

    role.delete()

    notify(
        user=user,
        notification_type=Notification.NotificationType.RESEARCHER_REJECTED,
        message=(
            f"Your researcher access has been revoked: {reason}"
        ),
        reason=reason,
    )

    return True