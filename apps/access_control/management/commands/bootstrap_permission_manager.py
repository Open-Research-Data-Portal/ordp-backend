from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db import models
from django.utils import timezone

from apps.access_control.models import Permission, UserPermissionGrant
from apps.access_control.services import audit


class Command(BaseCommand):
    help = "Give the initial permission-management grants to an existing superuser. Run once after migrate."

    def add_arguments(self, parser):
        parser.add_argument("--email", required=True)

    @transaction.atomic
    def handle(self, *args, **options):
        User = get_user_model()
        user = User.objects.filter(email__iexact=options["email"], is_active=True, is_superuser=True).first()
        if not user:
            raise CommandError("An active existing superuser with that email is required.")
        now = timezone.now()
        existing = UserPermissionGrant.objects.filter(permission_id="user.permission.grant", revoked_at__isnull=True).filter(
            models.Q(expires_at__isnull=True) | models.Q(expires_at__gt=now)
        ).exists()
        if existing:
            raise CommandError("A permission manager already exists. Use the API for subsequent grants.")
        for permission in Permission.objects.filter(resource_type="platform"):
            UserPermissionGrant.objects.create(user=user, permission=permission, resource_type="platform", granted_by=user)
            audit(user, "bootstrap.grant", user=user, permission=permission, resource_type="platform")
        self.stdout.write(self.style.SUCCESS("Permission manager bootstrapped."))
