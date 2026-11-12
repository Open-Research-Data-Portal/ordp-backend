from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("datasets", "0036_metadata_edit_audit"),
    ]

    operations = [
        migrations.AddField(
            model_name="datasetproposal",
            name="release_notes",
            field=models.TextField(blank=True),
        ),
    ]
