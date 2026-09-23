from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("datasets", "0023_dataset_is_archived_dataset_archived_at"),
        ("datasets", "0023_drop_archive_columns"),
    ]

    operations = [
        migrations.RunSQL(
            sql="""
                ALTER TABLE datasets_dataset
                ADD COLUMN IF NOT EXISTS is_archived boolean NOT NULL DEFAULT false;
                ALTER TABLE datasets_dataset
                ADD COLUMN IF NOT EXISTS archived_at timestamp with time zone NULL;
            """,
            reverse_sql=migrations.RunSQL.noop,
        ),
    ]
