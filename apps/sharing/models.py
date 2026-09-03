import uuid
from django.conf import settings
from django.db import models
from django.utils import timezone
from apps.datasets.models import Dataset


class SharePermission(models.Model):
    class AccessType(models.TextChoices):
        VIEW = "view"
        DOWNLOAD = "download"

    class Status(models.TextChoices):
        ACTIVE = "active"
        EXPIRED = "expired"
        REVOKED = "revoked"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    dataset = models.ForeignKey(Dataset, on_delete=models.CASCADE, related_name="share_permissions")
    shared_with_user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    access_type = models.CharField(max_length=16, choices=AccessType.choices, default=AccessType.DOWNLOAD)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ACTIVE)
    expires_at = models.DateTimeField(null=True, blank=True)
    granted_at = models.DateTimeField(auto_now_add=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    revoked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )



    def is_active_grant(self):
        """Lazy expiry check, same pattern as LoginSecurity.is_locked() —
        flips status to EXPIRED on read rather than needing a scheduled job."""
        if self.status != self.Status.ACTIVE:
            return False
        if self.expires_at and self.expires_at <= timezone.now():
            self.status = self.Status.EXPIRED
            self.save(update_fields=["status"])
            return False
        return True


class UsabilityFormResponse(models.Model):
    """Collected for every share request, regardless of visibility tier."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    dataset = models.ForeignKey(Dataset, on_delete=models.CASCADE, related_name="usability_forms")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.CASCADE)
    email = models.EmailField(blank=True)  
    purpose = models.TextField()
    submitted_at = models.DateTimeField(auto_now_add=True)


class RestrictedAccessJustification(models.Model):
    """The second, additional form — collected only for restricted-visibility requests."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    dataset = models.ForeignKey(Dataset, on_delete=models.CASCADE)
    requester = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.CASCADE)
    email = models.EmailField(blank=True)
    justification = models.TextField()
    submitted_at = models.DateTimeField(auto_now_add=True)


class DatasetAccessRequest(models.Model):
    """Restricted-visibility sharing request. Requires BOTH reviewer committee
    majority AND dataset-owner approval."""
    class Status(models.TextChoices):
        PENDING = "pending"
        APPROVED = "approved"
        REJECTED = "rejected"

    class PurposeType(models.TextChoices):
        READ = "read", "Read / analyze"
        EDIT = "edit", "Intends to propose changes"

    class OwnerDecision(models.TextChoices):
        PENDING = "pending"
        APPROVED = "approved"
        REJECTED = "rejected"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    dataset = models.ForeignKey(Dataset, on_delete=models.CASCADE, related_name="access_requests")
    requester = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.CASCADE, related_name="access_requests"
    )
    requester_email = models.EmailField()
    claim_token = models.CharField(max_length=64, unique=True, null=True, blank=True)
    claim_token_expires_at = models.DateTimeField(null=True, blank=True)
    shared_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+",
        help_text="Set when this request was created via share_with_user rather than the requester acting for themselves.",
    )
    usability_form = models.ForeignKey(UsabilityFormResponse, on_delete=models.CASCADE)
    restricted_justification = models.ForeignKey(RestrictedAccessJustification, null=True, blank=True, on_delete=models.CASCADE)
    purpose_type = models.CharField(max_length=16, choices=PurposeType.choices, default=PurposeType.READ)
    requested_duration_days = models.PositiveIntegerField(null=True, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    owner_decision = models.CharField(max_length=16, choices=OwnerDecision.choices, default=OwnerDecision.PENDING)
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