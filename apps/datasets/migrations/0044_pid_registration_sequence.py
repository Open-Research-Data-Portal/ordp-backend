
from django.db import migrations, models


FORWARD_SQL = """
CREATE SEQUENCE datasets_pidregistrationevent_sequence_seq;

WITH numbered AS (
    SELECT
        id,
        nextval('datasets_pidregistrationevent_sequence_seq') AS seq
    FROM datasets_pidregistrationevent
    ORDER BY created_at, id
)
UPDATE datasets_pidregistrationevent AS audit
SET sequence = numbered.seq
FROM numbered
WHERE audit.id = numbered.id;

ALTER SEQUENCE datasets_pidregistrationevent_sequence_seq
OWNED BY datasets_pidregistrationevent.sequence;

ALTER TABLE datasets_pidregistrationevent
ALTER COLUMN sequence
SET DEFAULT nextval('datasets_pidregistrationevent_sequence_seq');

ALTER TABLE datasets_pidregistrationevent
ALTER COLUMN sequence SET NOT NULL;
"""

REVERSE_SQL = """
ALTER TABLE datasets_pidregistrationevent
ALTER COLUMN sequence DROP DEFAULT;

DROP SEQUENCE IF EXISTS datasets_pidregistrationevent_sequence_seq;
"""


class Migration(migrations.Migration):

    dependencies = [
        ("datasets", "0043_datacite_relationship_types"),
    ]

    operations = [
        migrations.AddField(
            model_name="pidregistrationevent",
            name="sequence",
            field=models.BigIntegerField(
                editable=False,
                null=True,
                unique=True,
            ),
        ),
        migrations.RunSQL(
            sql=FORWARD_SQL,
            reverse_sql=REVERSE_SQL,
        ),
        migrations.AlterField(
            model_name="pidregistrationevent",
            name="sequence",
            field=models.BigIntegerField(
                editable=False,
                unique=True,
            ),
        ),
        migrations.AlterModelOptions(
            name="pidregistrationevent",
            options={"ordering": ["sequence"]},
        ),
    ]
