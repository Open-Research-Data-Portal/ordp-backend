import uuid
from django.conf import settings
from django.db import models


class Notification(models.Model):
    class NotificationType(models.TextChoices):
        UPLOAD_SUCCESS = "upload_success"
        UPLOAD_FAILURE = "upload_failure"
        NEW_VERSION_AVAILABLE = "new_version_available"
        DATASET_APPROVED = "dataset_approved"
        DATASET_REJECTED = "dataset_rejected"
        REVISION_PROPOSED = "revision_proposed"
        REVISION_REJECTED = "revision_rejected"
        CONTENT_UPDATE_PENDING = "content_update_pending"
        RESEARCHER_REQUEST = "researcher_request"
        CONTRIBUTOR_INVITATION = "contributor_invitation"
        ACCESS_REQUEST = "access_request"
        RESEARCHER_APPROVED = "researcher_approved"
        RESEARCHER_REJECTED = "researcher_rejected"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notifications")
    dataset = models.ForeignKey("datasets.Dataset", null=True, blank=True, on_delete=models.SET_NULL)
    notification_type = models.CharField(max_length=32, choices=NotificationType.choices)
    reason = models.TextField(blank=True, null=True)
    message = models.TextField()
    link_path = models.CharField(max_length=512, blank=True)
    is_read = models.BooleanField(default=False)
    email_sent = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]