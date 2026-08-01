import uuid
from django.conf import settings
from django.db import models
from apps.datasets.models import Dataset


class ModerationDecision(models.Model):
    class Decision(models.TextChoices):
        APPROVED = "approved"
        REJECTED = "rejected"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    dataset = models.ForeignKey(Dataset, on_delete=models.CASCADE, related_name="moderation_decisions")
    reviewer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    decision = models.CharField(max_length=16, choices=Decision.choices)
    reason = models.TextField(blank=True, null=True)
    decided_at = models.DateTimeField(auto_now_add=True)