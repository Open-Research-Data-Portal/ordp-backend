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
    """Model only in branch 1 — the invite endpoint (with its notification) lands in
    the sharing branch. This just lets a dataset display who's credited on it."""
    class ContributorType(models.TextChoices):
        AUTHOR = "author"
        CONTRIBUTOR = "contributor"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    dataset = models.ForeignKey(Dataset, on_delete=models.CASCADE, related_name="contributors")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    name = models.CharField(max_length=255, blank=True)
    contributor_type = models.CharField(max_length=16, choices=ContributorType.choices)
    order = models.IntegerField(default=1)