from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("metadata", "0013_seed_standard_characteristics"),
    ]

    operations = [
        migrations.AddField(
            model_name="category",
            name="available_for_interests",
            field=models.BooleanField(default=True),
        ),
    ]
