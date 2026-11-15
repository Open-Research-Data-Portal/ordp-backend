from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction

from apps.access_control.services import (
    ADMIN_PLATFORM_CODES, REVIEWER_PLATFORM_CODES, _system_grant_user,
)
from apps.accounts.models import UserProfile, UserRole
from apps.datasets.models import Contributor, PermissionLevel


class Command(BaseCommand):
    help = "Idempotently backfill legacy UserRole/upload/contributor rights into V2 grants."

    @transaction.atomic
    def handle(self, *args, **options):
        User = get_user_model()
        created = 0
        for user in User.objects.filter(is_active=True).select_related("profile"):
            profile = getattr(user, "profile", None)
            if not profile:
                continue
            roles = set(UserRole.objects.filter(profile=profile).values_list("role", flat=True))
            codes = []
            if UserRole.RoleChoice.ADMIN in roles or user.is_superuser:
                codes.extend(ADMIN_PLATFORM_CODES)
            if UserRole.RoleChoice.REVIEWER in roles:
                codes.extend(REVIEWER_PLATFORM_CODES)
            if profile.can_upload_datasets:
                codes.append("dataset.create")
            for code in sorted(set(codes)):
                _, was_created = _system_grant_user(user, code, "platform", None, actor=user)
                if was_created:
                    created += 1
        for contributor in Contributor.objects.filter(
            user__isnull=False, permission=PermissionLevel.EDIT,
        ).select_related("user", "dataset"):
            _, was_created = _system_grant_user(
                contributor.user, "dataset.edit", "dataset", contributor.dataset_id, actor=contributor.user,
            )
            if was_created:
                created += 1
        self.stdout.write(self.style.SUCCESS(f"Role backfill complete. grants_created={created}"))
