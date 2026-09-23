from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0023_merge_20260829_0811'),
    ]

    operations = [
        migrations.AddField(
            model_name='userprofile',
            name='profile_picture_key',
            field=models.CharField(blank=True, max_length=512),
        ),
    ]