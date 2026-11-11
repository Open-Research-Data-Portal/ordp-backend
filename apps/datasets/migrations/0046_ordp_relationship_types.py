from django.db import migrations, models



ORDP_RELATIONSHIP_GUARDS = r"""
CREATE OR REPLACE FUNCTION ordp_guard_resource_identifier() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'resource identifier history cannot be deleted'
            USING ERRCODE = '23514';
    END IF;
    IF TG_OP = 'INSERT' THEN
        IF NEW.registration_status = 'registered' THEN
            RAISE EXCEPTION 'identifiers can only be registered after a provider attempt'
                USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END IF;
    IF NEW.scheme IS DISTINCT FROM OLD.scheme
       OR NEW.role IS DISTINCT FROM OLD.role
       OR NEW.dataset_id IS DISTINCT FROM OLD.dataset_id
       OR NEW.release_id IS DISTINCT FROM OLD.release_id THEN
        RAISE EXCEPTION 'resource identifier identity and target are immutable'
            USING ERRCODE = '23514';
    END IF;
    IF OLD.identifier_value IS NOT NULL
       AND NEW.identifier_value IS DISTINCT FROM OLD.identifier_value THEN
        RAISE EXCEPTION 'persistent identifier values cannot be changed'
            USING ERRCODE = '23514';
    END IF;
    IF OLD.scheme = 'ordp'
       AND (
           NEW.landing_page_url IS DISTINCT FROM OLD.landing_page_url
           OR NEW.creation_source IS DISTINCT FROM OLD.creation_source
       ) THEN
        RAISE EXCEPTION 'ORDP PID resolver and provenance are immutable'
            USING ERRCODE = '23514';
    END IF;
    IF NEW.registration_status = 'registered'
       AND OLD.registration_status IS DISTINCT FROM 'registered'
       AND NOT EXISTS (
           SELECT 1 FROM datasets_doiregistrationattempt
            WHERE identifier_id = OLD.id AND status = 'succeeded'
       ) THEN
        RAISE EXCEPTION 'a successful provider attempt is required to register an identifier'
            USING ERRCODE = '23514';
    END IF;
    IF OLD.registration_status = 'registered'
       AND (
           NEW.registration_status IS DISTINCT FROM OLD.registration_status
           OR NEW.registered_at IS DISTINCT FROM OLD.registered_at
           OR NEW.provider IS DISTINCT FROM OLD.provider
       ) THEN
        RAISE EXCEPTION 'registered identifier history cannot be erased'
            USING ERRCODE = '23514';
    END IF;
    IF OLD.lifecycle_status = 'withdrawn'
       AND NEW.lifecycle_status IS DISTINCT FROM OLD.lifecycle_status THEN
        RAISE EXCEPTION 'a withdrawn resource identifier cannot be reactivated'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

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
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'resource relationship history cannot be deleted'
            USING ERRCODE = '23514';
    END IF;

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
    ELSIF NEW.relationship_type IN ('isVersionOf', 'isReleaseOf') THEN
        IF subject_role <> 'version' OR related_role <> 'concept' OR subject_dataset IS DISTINCT FROM related_dataset THEN
            RAISE EXCEPTION 'invalid version-to-concept relationship' USING ERRCODE = '23514';
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
    ELSIF NEW.relationship_type NOT IN (
        'isPartOf', 'isDerivedFrom', 'isSupplementTo',
        'isSupplementedBy', 'isIdenticalTo', 'relatedTo'
    ) THEN
        RAISE EXCEPTION 'unsupported relationship type' USING ERRCODE = '23514';
    END IF;

    IF EXISTS (
        SELECT 1
          FROM datasets_resourcerelationship AS existing
         WHERE existing.subject_identifier_id = NEW.subject_identifier_id
           AND existing.related_identifier_id = NEW.related_identifier_id
           AND (
               (NEW.relationship_type = 'hasVersion' AND existing.relationship_type IN ('isVersionOf', 'isReleaseOf'))
               OR (NEW.relationship_type IN ('isVersionOf', 'isReleaseOf') AND existing.relationship_type = 'hasVersion')
               OR (NEW.relationship_type = 'isNewVersionOf' AND existing.relationship_type = 'isPreviousVersionOf')
               OR (NEW.relationship_type = 'isPreviousVersionOf' AND existing.relationship_type = 'isNewVersionOf')
           )
    ) THEN
        RAISE EXCEPTION 'contradictory resource relationship already exists'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS ordp_resource_identifier_guard ON datasets_resourceidentifier;
CREATE TRIGGER ordp_resource_identifier_guard
BEFORE INSERT OR UPDATE OR DELETE ON datasets_resourceidentifier
FOR EACH ROW EXECUTE FUNCTION ordp_guard_resource_identifier();

DROP TRIGGER IF EXISTS ordp_resource_relationship_guard ON datasets_resourcerelationship;
CREATE TRIGGER ordp_resource_relationship_guard
BEFORE INSERT OR UPDATE OR DELETE ON datasets_resourcerelationship
FOR EACH ROW EXECUTE FUNCTION ordp_validate_resource_relationship();
"""


class Migration(migrations.Migration):
    dependencies = [
        ("datasets", "0045_ordp_owned_persistent_identifiers"),
    ]

    operations = [
        migrations.RunSQL(
            sql=ORDP_RELATIONSHIP_GUARDS,
            reverse_sql=migrations.RunSQL.noop,
        ),
        migrations.AlterField(
            model_name="resourcerelationship",
            name="relationship_type",
            field=models.CharField(
                choices=[
                    ("hasVersion", "Has version"),
                    ("isVersionOf", "Is version of"),
                    ("isReleaseOf", "Is release of"),
                    ("isNewVersionOf", "Is new version of"),
                    ("isPreviousVersionOf", "Is previous version of"),
                    ("isPartOf", "Is part of"),
                    ("isDerivedFrom", "Is derived from"),
                    ("isSupplementTo", "Is supplement to"),
                    ("isSupplementedBy", "Is supplemented by"),
                    ("isIdenticalTo", "Is identical to"),
                    ("relatedTo", "Related to"),
                ],
                max_length=32,
            ),
        ),
    ]
