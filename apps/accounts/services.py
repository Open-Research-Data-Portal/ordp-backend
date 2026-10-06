from calendar import monthrange

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from .models import ActivityLog, EmailVerificationToken, UserRole


AUTO_DEACTIVATION_ACTION = "USER_AUTO_DEACTIVATED"
MANUAL_DEACTIVATION_ACTION = "USER_MANUALLY_DEACTIVATED"
REACTIVATION_ACTION = "USER_REACTIVATED"
INACTIVITY_REASON = "SIX_MONTH_INACTIVITY"


def six_months_before(value):
    month_index = value.year * 12 + value.month - 1 - 6
    year, zero_based_month = divmod(month_index, 12)
    month = zero_based_month + 1
    day = min(value.day, monthrange(year, month)[1])
    return value.replace(year=year, month=month, day=day)


def account_was_activated(user):
    profile = getattr(user, "profile", None)
    if profile and profile.email_verified:
        return True
    has_activation_history = (
        EmailVerificationToken.objects.filter(user_id=user.pk, is_used=True).exists()
        or ActivityLog.objects.filter(
            user_id=user.pk,
            action__in=("email_verified", "login_success"),
        ).exists()
        or ActivityLog.objects.filter(
            target_object=str(user.pk),
            action__in=(
                "user_created",
                AUTO_DEACTIVATION_ACTION,
                MANUAL_DEACTIVATION_ACTION,
                REACTIVATION_ACTION,
            ),
        ).exists()
    )
    return has_activation_history


def deactivate_inactive_users(*, now=None, dry_run=False):
    user_model = get_user_model()
    now = now or timezone.now()
    cutoff = six_months_before(now)
    candidates = user_model.objects.filter(is_active=True).filter(
        Q(last_login__lt=cutoff)
        | Q(last_login__isnull=True, date_joined__lt=cutoff)
    ).values_list("pk", flat=True)
    affected_user_ids = []

    for user_id in candidates.iterator():
        with transaction.atomic():
            user = user_model.objects.select_for_update().get(pk=user_id)
            last_login = user.last_login
            inactivity_anchor = last_login or user.date_joined
            last_reactivation = ActivityLog.objects.filter(
                target_object=str(user.pk),
                action=REACTIVATION_ACTION,
            ).order_by("-timestamp").first()
            if last_reactivation and last_reactivation.timestamp > inactivity_anchor:
                inactivity_anchor = last_reactivation.timestamp
            is_inactive = inactivity_anchor < cutoff
            if not user.is_active or not is_inactive:
                continue

            profile = getattr(user, "profile", None)
            is_admin = bool(
                profile
                and profile.roles.filter(role=UserRole.RoleChoice.ADMIN).exists()
            )
            if is_admin:
                active_admin_count = user_model.objects.filter(
                    is_active=True,
                    profile__roles__role=UserRole.RoleChoice.ADMIN,
                ).values("pk").distinct().count()
                if active_admin_count <= 1:
                    continue

            affected_user_ids.append(user.pk)
            if dry_run:
                continue

            if profile and not profile.email_verified:
                profile.email_verified = True
                profile.save(update_fields=["email_verified"])

            user.is_active = False
            user.save(update_fields=["is_active"])
            ActivityLog.objects.create(
                user=None,
                action=AUTO_DEACTIVATION_ACTION,
                target_object=str(user.pk),
                ip_address="127.0.0.1",
                extra={
                    "reason": INACTIVITY_REASON,
                    "last_login": last_login.isoformat() if last_login else None,
                    "cutoff": cutoff.isoformat(),
                },
            )

    return {
        "cutoff": cutoff,
        "affected_count": len(affected_user_ids),
        "user_ids": affected_user_ids,
    }


# from requests import Response

# from apps.accounts.permissions import IsAdminOnly

# from .models import UserRole
# from apps.notifications.services import notify
# from apps.notifications.models import Notification
# from rest_framework.decorators import api_view, permission_classes
# @api_view(["POST"])
# @permission_classes([IsAdminOnly])
# def admin_grant_upload_access(request, user_id):
#     target_user = get_object_or_404(User, id=user_id)
#     profile = target_user.profile

#     if not profile.is_profile_complete():
#         return Response(
#             {"detail": "User must complete their profile first."},
#             status=400,
#         )

#     profile.can_upload_datasets = True
#     profile.save(update_fields=["can_upload_datasets"])

#     return Response({
#         "status": "granted",
#         "can_upload_datasets": True,
#     })


# @api_view(["POST"])
# @permission_classes([IsAdminOnly])
# def admin_revoke_upload_access(request, user_id):
#     target_user = get_object_or_404(User, id=user_id)
#     profile = target_user.profile

#     profile.can_upload_datasets = False
#     profile.save(update_fields=["can_upload_datasets"])

#     return Response({
#         "status": "revoked",
#         "can_upload_datasets": False,
#     })