from django.conf import settings
from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import UserProfile, UserRole


@receiver(post_save, sender=UserProfile)
def sync_primary_role(sender, instance, **kwargs):
    UserRole.objects.get_or_create(
        profile=instance,
        role=instance.role,
    )


@receiver(post_save, sender=settings.AUTH_USER_MODEL)
def create_user_profile(sender, instance, created, **kwargs):
    """Automatically create a profile whenever a new User is created."""

    if not created:
        return

    role = (
        UserProfile.Role.ADMIN
        if instance.is_superuser
        else UserProfile.Role.PUBLIC
    )

    profile, _ = UserProfile.objects.get_or_create(
        user=instance,
        defaults={
            "full_name": instance.get_full_name() or instance.username,
            "role": role,
        },
    )


    UserRole.objects.get_or_create(
        profile=profile,
        role=profile.role,
    )