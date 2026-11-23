import uuid
import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ("datasets", "0046_ordp_relationship_types"),
    ]

    operations = [
        migrations.CreateModel(
            name="AnalysisJob",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("job_type", models.CharField(db_index=True, default="analyse_file", max_length=64)),
                ("key", models.CharField(db_index=True, max_length=255, unique=True)),
                ("payload", models.JSONField(blank=True, default=dict)),
                (
                    "status",
                    models.CharField(
                        choices=[("queued", "Queued"), ("running", "Running"), ("done", "Done"), ("failed", "Failed")],
                        db_index=True,
                        default="queued",
                        max_length=32,
                    ),
                ),
                ("attempts", models.PositiveIntegerField(default=0)),
                ("max_attempts", models.PositiveIntegerField(default=3)),
                ("run_after", models.DateTimeField(db_index=True, default=django.utils.timezone.now)),
                ("locked_by", models.CharField(blank=True, max_length=255, null=True)),
                ("locked_at", models.DateTimeField(blank=True, null=True)),
                ("error", models.TextField(blank=True, default="")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "ordering": ["created_at"],
            },
        ),
        migrations.CreateModel(
            name="FileAnalysis",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("checksum_sha256", models.CharField(db_index=True, max_length=64)),
                (
                    "level",
                    models.CharField(
                        blank=True,
                        choices=[("A", "Level A"), ("B1", "Level B1"), ("B2", "Level B2"), ("C", "Level C")],
                        max_length=10,
                        null=True,
                    ),
                ),
                ("detected_type", models.CharField(blank=True, default="", max_length=64)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("queued", "Queued"),
                            ("running", "Running"),
                            ("done", "Done"),
                            ("partial", "Partial"),
                            ("failed", "Failed"),
                        ],
                        db_index=True,
                        default="queued",
                        max_length=32,
                    ),
                ),
                (
                    "coverage",
                    models.CharField(
                        blank=True,
                        choices=[("fully_analysed", "Fully Analysed"), ("partially_analysed", "Partially Analysed")],
                        max_length=32,
                        null=True,
                    ),
                ),
                ("checks", models.JSONField(blank=True, default=list)),
                ("technical", models.JSONField(blank=True, default=dict)),
                ("warnings", models.JSONField(blank=True, default=list)),
                ("error", models.JSONField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("finished_at", models.DateTimeField(blank=True, null=True)),
                (
                    "stored_object",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="ai_analysis",
                        to="datasets.storedobject",
                    ),
                ),
            ],
            options={
                "verbose_name_plural": "File analyses",
                "ordering": ["-created_at"],
            },
        ),
        migrations.AddIndex(
            model_name="analysisjob",
            index=models.Index(fields=["status", "run_after", "created_at"], name="ai_int_job_status_idx"),
        ),
    ]
