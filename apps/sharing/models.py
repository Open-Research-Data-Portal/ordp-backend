from django.db import models

# Create your models here.
import uuid
from django.conf import settings
from django.db import models
from apps.datasets.models import Dataset


class SharePermission(models.Model):
    class AccessType(models.TextChoices):
        VIEW = "view"
        DOWNLOAD = "download"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    dataset = models.ForeignKey(Dataset, on_delete=models.CASCADE, related_name="share_permissions")
    shared_with_user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    access_type = models.CharField(max_length=16, choices=AccessType.choices, default=AccessType.DOWNLOAD)
    expires_at = models.DateTimeField(null=True, blank=True)
    granted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ["dataset", "shared_with_user"]


class UsabilityFormResponse(models.Model):
    """Collected for every share request, regardless of visibility tier."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    dataset = models.ForeignKey(Dataset, on_delete=models.CASCADE, related_name="usability_forms")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    purpose = models.TextField()
    submitted_at = models.DateTimeField(auto_now_add=True)


class RestrictedAccessJustification(models.Model):
    """The second, additional form — collected only for restricted-visibility requests."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    dataset = models.ForeignKey(Dataset, on_delete=models.CASCADE)
    requester = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    justification = models.TextField()
    submitted_at = models.DateTimeField(auto_now_add=True)


class DatasetAccessRequest(models.Model):
    """Restricted-visibility sharing request. Decided by reviewer committee majority
    vote (see AccessRequestVote), not by the dataset owner."""
    class Status(models.TextChoices):
        PENDING = "pending"
        APPROVED = "approved"
        REJECTED = "rejected"

    class PurposeType(models.TextChoices):
        READ = "read", "Read / analyze"
        EDIT = "edit", "Intends to propose changes"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    dataset = models.ForeignKey(Dataset, on_delete=models.CASCADE, related_name="access_requests")
    requester = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    usability_form = models.ForeignKey(UsabilityFormResponse, on_delete=models.CASCADE)
    restricted_justification = models.ForeignKey(RestrictedAccessJustification, on_delete=models.CASCADE)
    purpose_type = models.CharField(max_length=16, choices=PurposeType.choices, default=PurposeType.READ)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    resolved_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class AccessRequestVote(models.Model):
    class Vote(models.TextChoices):
        APPROVE = "approve"
        REJECT = "reject"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    access_request = models.ForeignKey(DatasetAccessRequest, on_delete=models.CASCADE, related_name="votes")
    reviewer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    vote = models.CharField(max_length=16, choices=Vote.choices)
    voted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ["access_request", "reviewer"]