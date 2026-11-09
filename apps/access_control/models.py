import uuid
from datetime import timedelta

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone


def default_request_expiry():
    return timezone.now() + timedelta(days=getattr(settings, "ACCESS_REQUEST_EXPIRATION_DAYS", 30))


class Permission(models.Model):
    code = models.CharField(max_length=80, primary_key=True)
    name = models.CharField(max_length=120)
    description = models.TextField(blank=True)
    resource_type = models.CharField(max_length=24)
    action = models.CharField(max_length=40)


class Group(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=120, unique=True)
    description = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="created_access_groups")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    archived_at = models.DateTimeField(null=True, blank=True)
    members = models.ManyToManyField(settings.AUTH_USER_MODEL, through="GroupMembership", through_fields=("group", "user"), related_name="access_groups")
    permissions = models.ManyToManyField(Permission, through="GroupPermissionGrant", related_name="access_groups")


class GroupMembership(models.Model):
    group = models.ForeignKey(Group, on_delete=models.CASCADE, related_name="memberships")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="access_memberships")
    added_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="added_access_memberships")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["group", "user"], name="unique_access_group_member")]


class Grant(models.Model):
    class ResourceType(models.TextChoices):
        PLATFORM = "platform", "Platform"
        DATASET = "dataset", "Dataset"
        DATASET_FILE = "dataset_file", "Dataset file"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    permission = models.ForeignKey(Permission, on_delete=models.PROTECT)
    resource_type = models.CharField(max_length=24, choices=ResourceType.choices)
    resource_id = models.UUIDField(null=True, blank=True)
    granted_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="issued_%(class)s")
    granted_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        abstract = True
        constraints = [models.CheckConstraint(condition=(Q(resource_type="platform", resource_id__isnull=True) | Q(resource_type__in=["dataset", "dataset_file"], resource_id__isnull=False)), name="%(class)s_valid_scope")]


class UserPermissionGrant(Grant):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="direct_permission_grants")

    class Meta(Grant.Meta):
        constraints = Grant.Meta.constraints + [
            models.UniqueConstraint(fields=["user", "permission"], condition=Q(revoked_at__isnull=True, resource_type="platform"), name="unique_live_user_platform_grant"),
            models.UniqueConstraint(fields=["user", "permission", "resource_type", "resource_id"], condition=Q(revoked_at__isnull=True) & ~Q(resource_type="platform"), name="unique_live_user_resource_grant"),
        ]
        indexes = [models.Index(fields=["user", "resource_type", "resource_id"])]


class GroupPermissionGrant(Grant):
    group = models.ForeignKey(Group, on_delete=models.CASCADE, related_name="permission_grants")

    class Meta(Grant.Meta):
        constraints = Grant.Meta.constraints + [
            models.UniqueConstraint(fields=["group", "permission"], condition=Q(revoked_at__isnull=True, resource_type="platform"), name="unique_live_group_platform_grant"),
            models.UniqueConstraint(fields=["group", "permission", "resource_type", "resource_id"], condition=Q(revoked_at__isnull=True) & ~Q(resource_type="platform"), name="unique_live_group_resource_grant"),
        ]
        indexes = [models.Index(fields=["group", "resource_type", "resource_id"])]


class AccessRequest(models.Model):
    class Kind(models.TextChoices):
        PERMISSION = "permission", "User permission"
        GROUP_MEMBERSHIP = "group_membership", "Group membership"
        GROUP_PERMISSION = "group_permission", "Group permission"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        APPROVED = "approved", "Approved"
        PARTIAL = "partially_approved", "Partially approved"
        REJECTED = "rejected", "Rejected"
        CANCELLED = "cancelled", "Cancelled"
        EXPIRED = "expired", "Expired"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    requester = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="permission_requests")
    kind = models.CharField(max_length=24, choices=Kind.choices, default=Kind.PERMISSION)
    group = models.ForeignKey(Group, null=True, blank=True, on_delete=models.PROTECT, related_name="access_requests")
    resource_type = models.CharField(max_length=24, choices=Grant.ResourceType.choices)
    resource_id = models.UUIDField(null=True, blank=True)
    justification = models.TextField()
    status = models.CharField(max_length=24, choices=Status.choices, default=Status.PENDING)
    review_comment = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(default=default_request_expiry)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="reviewed_permission_requests")
    permissions = models.ManyToManyField(Permission, through="AccessRequestPermission")

    class Meta:
        indexes = [models.Index(fields=["status", "created_at"])]


class AccessRequestPermission(models.Model):
    class Decision(models.TextChoices):
        PENDING = "pending", "Pending"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"

    request = models.ForeignKey(AccessRequest, on_delete=models.CASCADE, related_name="items")
    permission = models.ForeignKey(Permission, on_delete=models.PROTECT)
    decision = models.CharField(max_length=12, choices=Decision.choices, default=Decision.PENDING)
    decision_reason = models.TextField(blank=True)
    approved_expiration = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["request", "permission"], name="unique_requested_permission")]


class PermissionAuditLog(models.Model):
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="permission_audit_actions")
    target_user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="permission_audit_targets")
    target_group = models.ForeignKey(Group, null=True, on_delete=models.SET_NULL)
    permission = models.ForeignKey(Permission, null=True, on_delete=models.SET_NULL)
    resource_type = models.CharField(max_length=24, blank=True)
    resource_id = models.UUIDField(null=True, blank=True)
    action = models.CharField(max_length=48)
    timestamp = models.DateTimeField(auto_now_add=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        indexes = [models.Index(fields=["timestamp"])]
