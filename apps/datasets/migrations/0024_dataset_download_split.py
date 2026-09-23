from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('datasets', '0023_dataset_is_archived_dataset_archived_at'),
    ]

    operations = [
        migrations.AddField(
            model_name='dataset',
            name='access_download_count',
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name='dataset',
            name='modification_download_count',
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name='dataset',
            name='archived_access_downloads',
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='dataset',
            name='archived_modification_downloads',
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
    ]