import uuid
from django.db import models
from django.utils import timezone


class AnalysisJob(models.Model):
    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        RUNNING = "running", "Running"
        DONE = "done", "Done"
        FAILED = "failed", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    job_type = models.CharField(max_length=64, default="analyse_file", db_index=True)
    key = models.CharField(max_length=255, unique=True, db_index=True)
    payload = models.JSONField(default=dict, blank=True)
    status = models.CharField(
        max_length=32,
        choices=Status.choices,
        default=Status.QUEUED,
        db_index=True,
    )
    attempts = models.PositiveIntegerField(default=0)
    max_attempts = models.PositiveIntegerField(default=3)
    run_after = models.DateTimeField(default=timezone.now, db_index=True)
    locked_by = models.CharField(max_length=255, null=True, blank=True)
    locked_at = models.DateTimeField(null=True, blank=True)
    error = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["created_at"]
        indexes = [
            models.Index(fields=["status", "run_after", "created_at"], name="ai_int_job_status_idx"),
        ]

    def __str__(self):
        return f"AnalysisJob({self.job_type}, {self.key}, status={self.status})"


class FileAnalysis(models.Model):
    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        RUNNING = "running", "Running"
        DONE = "done", "Done"
        PARTIAL = "partial", "Partial"
        FAILED = "failed", "Failed"

    class Level(models.TextChoices):
        A = "A", "Level A"
        B1 = "B1", "Level B1"
        B2 = "B2", "Level B2"
        C = "C", "Level C"

    class Coverage(models.TextChoices):
        FULLY_ANALYSED = "fully_analysed", "Fully Analysed"
        PARTIALLY_ANALYSED = "partially_analysed", "Partially Analysed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    stored_object = models.OneToOneField(
        "datasets.StoredObject",
        on_delete=models.CASCADE,
        related_name="ai_analysis",
    )
    checksum_sha256 = models.CharField(max_length=64, db_index=True)
    level = models.CharField(
        max_length=10,
        choices=Level.choices,
        null=True,
        blank=True,
    )
    detected_type = models.CharField(max_length=64, blank=True, default="")
    status = models.CharField(
        max_length=32,
        choices=Status.choices,
        default=Status.QUEUED,
        db_index=True,
    )
    coverage = models.CharField(
        max_length=32,
        choices=Coverage.choices,
        null=True,
        blank=True,
    )
    checks = models.JSONField(default=list, blank=True)
    technical = models.JSONField(default=dict, blank=True)
    warnings = models.JSONField(default=list, blank=True)
    error = models.JSONField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name_plural = "File analyses"

    def __str__(self):
        return f"FileAnalysis(stored_object={self.stored_object_id}, level={self.level}, status={self.status})"
