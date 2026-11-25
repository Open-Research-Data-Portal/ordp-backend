import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("datasets", "0024_revisionrequest_additional_justification_and_more"),
    ]

    operations = [
        migrations.CreateModel(
            name="ResearchProject",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("name", models.CharField(max_length=255)),
                ("description", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("archived_at", models.DateTimeField(blank=True, null=True)),
                ("created_by", models.ForeignKey(
                    null=True, on_delete=django.db.models.deletion.SET_NULL,
                    related_name="created_research_projects", to=settings.AUTH_USER_MODEL,
                )),
                ("datasets", models.ManyToManyField(blank=True, related_name="research_projects", to="datasets.dataset")),
                ("members", models.ManyToManyField(blank=True, related_name="research_projects", to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.CreateModel(
            name="DatasetCollection",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("name", models.CharField(max_length=255)),
                ("description", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("archived_at", models.DateTimeField(blank=True, null=True)),
                ("created_by", models.ForeignKey(
                    null=True, on_delete=django.db.models.deletion.SET_NULL,
                    related_name="created_dataset_collections", to=settings.AUTH_USER_MODEL,
                )),
                ("datasets", models.ManyToManyField(blank=True, related_name="collections", to="datasets.dataset")),
            ],
        ),
    ]
