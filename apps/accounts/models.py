from django.conf import settings
from django.db import models
from django.utils import timezone
from datetime import timedelta


class LoginSecurity(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="login_security",
    )
    failed_attempts = models.PositiveSmallIntegerField(default=0)
    locked_until = models.DateTimeField(null=True, blank=True)

    def is_locked(self):
        if self.locked_until and self.locked_until <= timezone.now():
            self.failed_attempts = 0
            self.locked_until = None
            self.save()
            return False
        return self.locked_until is not None

    def register_failed_attempt(self):
        self.failed_attempts += 1
        if self.failed_attempts >= 3:
            self.locked_until = timezone.now() + timedelta(minutes=15)
        self.save()

    def reset(self):
        self.failed_attempts = 0
        self.locked_until = None
        self.save()