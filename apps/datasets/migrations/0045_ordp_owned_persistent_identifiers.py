from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("datasets", "0044_pid_registration_sequence"),
    ]

    operations = [
        migrations.AddField(
            model_name="resourceidentifier",
            name="creation_source",
            field=models.CharField(blank=True, default="system", max_length=64),
        ),
        migrations.AlterField(
            model_name="resourceidentifier",
            name="scheme",
            field=models.CharField(
                choices=[("doi", "DOI"), ("ordp", "ORDP internal PID")],
                default="doi",
                max_length=16,
            ),
        ),
        migrations.RemoveConstraint(
            model_name="resourceidentifier",
            name="registered_identifier_has_value",
        ),
        migrations.AddConstraint(
            model_name="resourceidentifier",
            constraint=models.CheckConstraint(
                condition=(
                    models.Q(scheme="ordp", identifier_value__isnull=False)
                    & ~models.Q(identifier_value="")
                    & models.Q(registration_status="not_requested")
                    & models.Q(registered_at__isnull=True)
                    & models.Q(provider="")
                ) | (
                    models.Q(scheme="doi")
                    & (
                        (
                            models.Q(registration_status="registered")
                            & models.Q(identifier_value__isnull=False)
                            & ~models.Q(identifier_value="")
                            & models.Q(registered_at__isnull=False)
                        ) | (
                            ~models.Q(registration_status="registered")
                            & models.Q(identifier_value__isnull=True)
                        )
                    )
                ),
                name="registered_identifier_has_value",
            ),
        ),
    ]
