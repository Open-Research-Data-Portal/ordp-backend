from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('datasets', '0022_remove_dataset_embargo_end_date_and_more'),
    ]

    operations = [
        migrations.RunSQL(
            sql="""
                ALTER TABLE datasets_dataset DROP COLUMN IF EXISTS is_archived;
                ALTER TABLE datasets_dataset DROP COLUMN IF EXISTS archived_at;
            """,
            reverse_sql="""
                ALTER TABLE datasets_dataset ADD COLUMN is_archived boolean NOT NULL DEFAULT false;
                ALTER TABLE datasets_dataset ADD COLUMN archived_at timestamp with time zone NULL;
            """,
        ),
    ]