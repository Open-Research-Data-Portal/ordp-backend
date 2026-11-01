from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0027_alter_blockedcredential_credential_type"),
    ]

    operations = [
        migrations.AlterField(
            model_name="blockedcredential",
            name="credential_type",
            field=models.CharField(
                choices=[("email", "Email"), ("username", "Username")],
                max_length=20,
            ),
        ),
    ]
