from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("datasets", "0037_datasetproposal_release_notes"),
    ]

    operations = [
        migrations.AddField(
            model_name="uploadsession",
            name="proposal",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="upload_sessions",
                to="datasets.datasetproposal",
            ),
        ),
    ]
