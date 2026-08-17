from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import UserProfile, UserRole


@receiver(post_save, sender=UserProfile)
def sync_primary_role(sender, instance, **kwargs):
    """Whenever a profile is saved, make sure its `role` field is reflected in the
    UserRole table. This is additive only — it never removes a role, so code that
    grants a second role via UserRole directly (e.g. decide_researcher_request)
    is never undone by a later save() that touches unrelated fields."""
    UserRole.objects.get_or_create(profile=instance, role=instance.role)