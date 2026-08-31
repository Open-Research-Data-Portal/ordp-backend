from django.db import migrations


def seed_characteristics(apps, schema_editor):
    DatasetCharacteristic = apps.get_model(
        "metadata",
        "DatasetCharacteristic",
    )

    characteristics = [
        "Tabular",
        "Time-series",
        "Geospatial",
        "Image",
        "Audio",
    ]

    for name in characteristics:
        DatasetCharacteristic.objects.get_or_create(
            name=name,
            defaults={
                "status": "approved",
            },
        )


def remove_characteristics(apps, schema_editor):
    DatasetCharacteristic = apps.get_model(
        "metadata",
        "DatasetCharacteristic",
    )

    DatasetCharacteristic.objects.filter(
        name__in=[
            "Tabular",
            "Time-series",
            "Geospatial",
            "Image",
            "Audio",
        ]
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("metadata", "0012_metadata_characteristics_metadata_languages"),
    ]

    operations = [
        migrations.RunPython(
            seed_characteristics,
            remove_characteristics,
        ),
    ]