from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("notifications", "0007_alter_notification_notification_type"),
    ]

    operations = [
        migrations.AddField(
            model_name="notification",
            name="is_deleted",
            field=models.BooleanField(default=False),
        ),
    ]