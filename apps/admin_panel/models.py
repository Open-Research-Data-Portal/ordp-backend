import uuid
from django.conf import settings
from django.db import models
from apps.datasets.models import Dataset


class ModerationDecision(models.Model):
    class Decision(models.TextChoices):
        APPROVED = "approved"
        CHANGES_REQUESTED = "changes_requested"
        REJECTED = "rejected"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    dataset = models.ForeignKey(Dataset, on_delete=models.CASCADE, related_name="moderation_decisions")
    reviewer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    decision = models.CharField(max_length=18, choices=Decision.choices)
    reason = models.TextField(blank=True, null=True)
    decided_at = models.DateTimeField(auto_now_add=True)

class DatasetReviewerAssignment(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    dataset = models.ForeignKey(
        Dataset,
        on_delete=models.CASCADE,
        related_name="reviewer_assignments",
    )
    reviewer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="dataset_reviewer_assignments",
    )
    assigned_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ["dataset", "reviewer"]
class ThumbnailSuggestion(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    dataset = models.ForeignKey("datasets.Dataset", on_delete=models.CASCADE, related_name="thumbnail_suggestions")
    reviewer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    fallback_thumbnail = models.ForeignKey("metadata.FallbackThumbnail", on_delete=models.CASCADE)
    suggested_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ["dataset", "reviewer"]


class DatasetDeletionRequest(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending"
        APPROVED = "approved"
        REJECTED = "rejected"
        EXECUTED = "executed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    dataset = models.ForeignKey("datasets.Dataset", on_delete=models.SET_NULL, null=True, related_name="deletion_requests")
    dataset_title = models.CharField(max_length=255, blank=True)
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    reason = models.TextField()
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    resolved_at = models.DateTimeField(null=True, blank=True)
    executed_at = models.DateTimeField(null=True, blank=True)
    executed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)


class DeletionRequestVote(models.Model):
    class Vote(models.TextChoices):
        APPROVE = "approve"
        REJECT = "reject"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    deletion_request = models.ForeignKey(DatasetDeletionRequest, on_delete=models.CASCADE, related_name="votes")
    reviewer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    vote = models.CharField(max_length=16, choices=Vote.choices)
    voted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ["deletion_request", "reviewer"]