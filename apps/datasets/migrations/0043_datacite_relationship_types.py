from django.db import migrations, models


REPLACE_RELATIONSHIP_GUARD = r"""
CREATE OR REPLACE FUNCTION ordp_validate_resource_relationship() RETURNS trigger AS $$
DECLARE
    subject_role varchar(16);
    related_role varchar(16);
    subject_dataset uuid;
    related_dataset uuid;
    subject_scheme varchar(16);
    related_scheme varchar(16);
    subject_major integer;
    subject_minor integer;
    subject_patch integer;
    related_major integer;
    related_minor integer;
    related_patch integer;
BEGIN
    SELECT identifier.role, COALESCE(identifier.dataset_id, release.dataset_id),
           identifier.scheme, release.major, release.minor, release.patch
      INTO subject_role, subject_dataset, subject_scheme,
           subject_major, subject_minor, subject_patch
      FROM datasets_resourceidentifier AS identifier
      LEFT JOIN datasets_datasetrelease AS release ON release.id = identifier.release_id
     WHERE identifier.id = NEW.subject_identifier_id;
    SELECT identifier.role, COALESCE(identifier.dataset_id, release.dataset_id),
           identifier.scheme, release.major, release.minor, release.patch
      INTO related_role, related_dataset, related_scheme,
           related_major, related_minor, related_patch
      FROM datasets_resourceidentifier AS identifier
      LEFT JOIN datasets_datasetrelease AS release ON release.id = identifier.release_id
     WHERE identifier.id = NEW.related_identifier_id;

    IF subject_role IS NULL OR related_role IS NULL
       OR subject_scheme IS DISTINCT FROM related_scheme
       OR NEW.subject_identifier_id = NEW.related_identifier_id THEN
        RAISE EXCEPTION 'invalid resource identifier relationship'
            USING ERRCODE = '23514';
    END IF;

    IF NEW.relationship_type = 'hasVersion' THEN
        IF subject_role <> 'concept' OR related_role <> 'version' OR subject_dataset IS DISTINCT FROM related_dataset THEN
            RAISE EXCEPTION 'invalid hasVersion relationship' USING ERRCODE = '23514';
        END IF;
    ELSIF NEW.relationship_type = 'isVersionOf' THEN
        IF subject_role <> 'version' OR related_role <> 'concept' OR subject_dataset IS DISTINCT FROM related_dataset THEN
            RAISE EXCEPTION 'invalid isVersionOf relationship' USING ERRCODE = '23514';
        END IF;
    ELSIF NEW.relationship_type IN ('isNewVersionOf', 'isPreviousVersionOf') THEN
        IF subject_role <> 'version' OR related_role <> 'version'
           OR subject_dataset IS DISTINCT FROM related_dataset THEN
            RAISE EXCEPTION 'version relationship requires versions of the same dataset' USING ERRCODE = '23514';
        END IF;
        IF NEW.relationship_type = 'isNewVersionOf'
           AND ROW(subject_major, subject_minor, subject_patch) <= ROW(related_major, related_minor, related_patch) THEN
            RAISE EXCEPTION 'isNewVersionOf must point from newer to older version' USING ERRCODE = '23514';
        END IF;
        IF NEW.relationship_type = 'isPreviousVersionOf'
           AND ROW(subject_major, subject_minor, subject_patch) >= ROW(related_major, related_minor, related_patch) THEN
            RAISE EXCEPTION 'isPreviousVersionOf must point from older to newer version' USING ERRCODE = '23514';
        END IF;
    ELSIF NEW.relationship_type NOT IN ('isPartOf', 'isDerivedFrom', 'isSupplementTo', 'isSupplementedBy', 'isIdenticalTo') THEN
        RAISE EXCEPTION 'unsupported DataCite relationship type' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""


class Migration(migrations.Migration):
    dependencies = [("datasets", "0042_pid_attempt_and_transition_audit")]

    operations = [
        migrations.AlterField(
            model_name="resourcerelationship",
            name="relationship_type",
            field=models.CharField(max_length=32, choices=[
                ("hasVersion", "Has version"),
                ("isVersionOf", "Is version of"),
                ("isNewVersionOf", "Is new version of"),
                ("isPreviousVersionOf", "Is previous version of"),
                ("isPartOf", "Is part of"),
                ("isDerivedFrom", "Is derived from"),
                ("isSupplementTo", "Is supplement to"),
                ("isSupplementedBy", "Is supplemented by"),
                ("isIdenticalTo", "Is identical to"),
            ]),
        ),
        migrations.RunSQL(REPLACE_RELATIONSHIP_GUARD, migrations.RunSQL.noop),
    ]
