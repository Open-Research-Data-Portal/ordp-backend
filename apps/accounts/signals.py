from django.conf import settings
from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import UserProfile, UserRole


@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def create_user_profile(sender, instance, created, **kwargs):
    """Automatically create a profile and assign the default role for new users."""

    if not created:
        return

    role = (
        UserRole.RoleChoice.ADMIN
        if instance.is_superuser
        else UserRole.RoleChoice.PUBLIC
    )

    profile, _ = UserProfile.objects.get_or_create(
        user=instance,
        defaults={
            "full_name": instance.get_full_name() or instance.username,
        },
    )

    UserRole.objects.get_or_create(
        profile=profile,
        role=role,
    )