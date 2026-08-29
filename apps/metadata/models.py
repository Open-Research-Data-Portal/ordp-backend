import uuid
from django.db import models
from django.conf import settings

class Category(models.Model):
    class Status(models.TextChoices):
        APPROVED = "approved"
        PENDING = "pending"
        REJECTED = "rejected"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=128, unique=True)
    description = models.TextField(blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.APPROVED)
    suggested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    def __str__(self):
        return self.name

class Keyword(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    word = models.CharField(max_length=64, unique=True)

    def __str__(self):
        return self.word


class Metadata(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    dataset = models.OneToOneField("datasets.Dataset", on_delete=models.CASCADE, related_name="metadata")
    description = models.TextField()
    category = models.ForeignKey(Category, on_delete=models.PROTECT)
    keywords = models.ManyToManyField(Keyword, blank=True, related_name="metadata_set")
    sponsor_or_grant = models.CharField(max_length=255, blank=True)
    doi_citation = models.CharField(max_length=255, blank=True)
    collaborators_text = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    related_resources = models.TextField(blank=True)
    geographic_coverage = models.CharField(max_length=255, blank=True)
    temporal_coverage = models.CharField(max_length=255, blank=True)

    has_header = models.BooleanField(null=True, blank=True)
    has_missing_values = models.BooleanField(null=True, blank=True)

    instances_represent = models.TextField(blank=True)
    collection_method = models.TextField(blank=True)
    recommended_data_splits = models.TextField(blank=True)
    sensitive_data_disclosure = models.TextField(blank=True)
    data_preprocessing = models.TextField(blank=True)
    citation_notes = models.TextField(blank=True)


class FallbackThumbnail(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    category = models.ForeignKey(Category, on_delete=models.CASCADE, related_name="fallback_thumbnails")
    image_key = models.CharField(max_length=512)
    usage_count = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["usage_count"]


class Language(models.Model):
    class Status(models.TextChoices):
        APPROVED = "approved"
        PENDING = "pending"
        REJECTED = "rejected"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=64, unique=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.APPROVED)
    suggested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    def __str__(self):
        return self.name


class DatasetCharacteristic(models.Model):
    class Status(models.TextChoices):
        APPROVED = "approved"
        PENDING = "pending"
        REJECTED = "rejected"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=128, unique=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.APPROVED)
    suggested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    def __str__(self):
        return self.name