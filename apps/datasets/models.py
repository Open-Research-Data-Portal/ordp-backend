import uuid
from django.conf import settings
from django.db import models


class Dataset(models.Model):
    class Visibility(models.TextChoices):
        PUBLIC = "public"
        INSTITUTIONAL = "institutional"
        RESTRICTED = "restricted"

    class Status(models.TextChoices):
        DRAFT = "draft"
        PENDING = "pending"
        APPROVED = "approved"
        REJECTED = "rejected"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    title = models.CharField(max_length=255)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="datasets")
    visibility = models.CharField(max_length=16, choices=Visibility.choices, default=Visibility.RESTRICTED)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    is_active = models.BooleanField(default=True)
    version = models.IntegerField(default=1)


    terms_accepted = models.BooleanField(default=False)
    terms_accepted_at = models.DateTimeField(null=True, blank=True)
    terms_version = models.CharField(max_length=16, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    assigned_reviewer = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name="assigned_datasets",
    )

    def __str__(self):
        return self.title


class DatasetFile(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    dataset = models.ForeignKey(Dataset, on_delete=models.CASCADE, related_name="files")
    file_key = models.CharField(max_length=512)
    file_type = models.CharField(max_length=32)
    file_size = models.BigIntegerField()
    checksum = models.CharField(max_length=128)
    uploaded_at = models.DateTimeField(auto_now_add=True)


class Contributor(models.Model):
    class ContributorType(models.TextChoices):
        AUTHOR = "author"
        CONTRIBUTOR = "contributor"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    dataset = models.ForeignKey(Dataset, on_delete=models.CASCADE, related_name="contributors")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    name = models.CharField(max_length=255, blank=True)
    invited_email = models.EmailField(blank=True) 
    contributor_type = models.CharField(max_length=16, choices=ContributorType.choices)
    order = models.IntegerField(default=1)

class DatasetRevision(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending"
        APPROVED = "approved"
        REJECTED = "rejected"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    dataset = models.ForeignKey(Dataset, on_delete=models.CASCADE, related_name="revisions")
    submitted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    previous_file_key = models.CharField(max_length=512)
    new_file_key = models.CharField(max_length=512)
    diff_percentage = models.FloatField()
    triggered_version_bump = models.BooleanField(default=False)
    submitter_message = models.TextField()
    change_summary = models.JSONField(default=dict, blank=True)
    proposed_metadata = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    created_at = models.DateTimeField(auto_now_add=True)


class PendingContentUpdate(models.Model):
    class Source(models.TextChoices):
        OWNER_EDIT = "owner_edit"
        CONTRIBUTOR_EDIT = "contributor_edit"
        REVISION = "revision"

    class Status(models.TextChoices):
        PENDING = "pending"
        APPROVED = "approved"
        REJECTED = "rejected"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    dataset = models.ForeignKey(Dataset, on_delete=models.CASCADE, related_name="pending_updates")
    source = models.CharField(max_length=16, choices=Source.choices)
    submitted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+")
    approved_by_owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+"
    )
    new_file_key = models.CharField(max_length=512)
    proposed_metadata = models.JSONField(default=dict, blank=True)
    diff_percentage = models.FloatField()
    change_summary = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    decided_at = models.DateTimeField(null=True, blank=True)


class DatasetVersion(models.Model):
    class Source(models.TextChoices):
        OWNER_EDIT = "owner_edit"
        CONTRIBUTOR_EDIT = "contributor_edit"
        REVISION = "revision"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    dataset = models.ForeignKey(Dataset, on_delete=models.CASCADE, related_name="versions")
    version_number = models.IntegerField()
    file_key = models.CharField(max_length=512)
    source = models.CharField(max_length=16, choices=Source.choices)
    changed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+")
    change_summary = models.JSONField(default=dict, blank=True)
    diff_percentage = models.FloatField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-version_number"]
        constraints = [
            models.UniqueConstraint(fields=["dataset", "version_number"], name="unique_dataset_version_number")
        ]