from django.db import migrations, models


POSTGRESQL_FUNCTIONS_AND_TRIGGERS = [
    """
    CREATE FUNCTION ordp_validate_dataset_release_pointer() RETURNS trigger AS $$
    BEGIN
        IF NEW.published_release_id IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM datasets_datasetrelease
            WHERE id = NEW.published_release_id AND dataset_id = NEW.id
              AND status = 'published'
        ) THEN
            RAISE EXCEPTION 'published release must belong to the same dataset'
                USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END;
    $$ LANGUAGE plpgsql
    """,
    """
    CREATE TRIGGER ordp_dataset_release_pointer_guard
    BEFORE INSERT OR UPDATE OF published_release_id ON datasets_dataset
    FOR EACH ROW EXECUTE FUNCTION ordp_validate_dataset_release_pointer()
    """,
    """
    CREATE FUNCTION ordp_validate_release_dataset_links() RETURNS trigger AS $$
    BEGIN
        IF NEW.base_release_id IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM datasets_datasetrelease
            WHERE id = NEW.base_release_id AND dataset_id = NEW.dataset_id
        ) THEN
            RAISE EXCEPTION 'base release must belong to the same dataset'
                USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END;
    $$ LANGUAGE plpgsql
    """,
    """
    CREATE TRIGGER ordp_release_dataset_guard
    BEFORE INSERT OR UPDATE OF dataset_id, base_release_id ON datasets_datasetrelease
    FOR EACH ROW EXECUTE FUNCTION ordp_validate_release_dataset_links()
    """,
    """
    CREATE FUNCTION ordp_validate_proposal_dataset_links() RETURNS trigger AS $$
    BEGIN
        IF NOT EXISTS (
            SELECT 1 FROM datasets_datasetrelease
            WHERE id = NEW.base_release_id AND dataset_id = NEW.dataset_id
        ) THEN
            RAISE EXCEPTION 'proposal base release must belong to the same dataset'
                USING ERRCODE = '23514';
        END IF;
        IF NEW.resulting_release_id IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM datasets_datasetrelease
            WHERE id = NEW.resulting_release_id AND dataset_id = NEW.dataset_id
        ) THEN
            RAISE EXCEPTION 'proposal result release must belong to the same dataset'
                USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END;
    $$ LANGUAGE plpgsql
    """,
    """
    CREATE TRIGGER ordp_proposal_dataset_guard
    BEFORE INSERT OR UPDATE OF dataset_id, base_release_id, resulting_release_id
    ON datasets_datasetproposal
    FOR EACH ROW EXECUTE FUNCTION ordp_validate_proposal_dataset_links()
    """,
    """
    CREATE FUNCTION ordp_validate_release_file_links() RETURNS trigger AS $$
    DECLARE
        release_dataset uuid;
        release_status varchar(16);
    BEGIN
        IF TG_OP = 'DELETE' THEN
            SELECT status INTO release_status
            FROM datasets_datasetrelease WHERE id = OLD.release_id;
            IF release_status = 'draft' THEN
                RETURN OLD;
            END IF;
            RAISE EXCEPTION 'published release manifests are immutable'
                USING ERRCODE = '23514';
        END IF;

        SELECT dataset_id, status INTO release_dataset, release_status
        FROM datasets_datasetrelease WHERE id = NEW.release_id;
        IF release_status IS DISTINCT FROM 'draft' THEN
                RAISE EXCEPTION 'release files can only be added while a release is being assembled'
                    USING ERRCODE = '23514';
        END IF;
        IF NOT EXISTS (
            SELECT 1 FROM datasets_storedobject
            WHERE id = NEW.stored_object_id AND dataset_id = release_dataset
        ) THEN
            RAISE EXCEPTION 'release file object must belong to the release dataset'
                USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END;
    $$ LANGUAGE plpgsql
    """,
    """
    CREATE TRIGGER ordp_release_file_immutable
    BEFORE INSERT OR UPDATE OR DELETE ON datasets_datasetreleasefile
    FOR EACH ROW EXECUTE FUNCTION ordp_validate_release_file_links()
    """,
    """
    CREATE FUNCTION ordp_validate_proposal_file_links() RETURNS trigger AS $$
    DECLARE
        proposal_status varchar(16);
    BEGIN
        SELECT status INTO proposal_status
        FROM datasets_datasetproposal WHERE id = NEW.proposal_id;
        IF proposal_status IS DISTINCT FROM 'draft' THEN
            RAISE EXCEPTION 'only draft proposal manifests can be edited'
                USING ERRCODE = '23514';
        END IF;
        IF NOT EXISTS (
            SELECT 1
            FROM datasets_datasetproposal AS proposal
            JOIN datasets_storedobject AS stored_object
              ON stored_object.id = NEW.stored_object_id
            WHERE proposal.id = NEW.proposal_id
              AND proposal.dataset_id = stored_object.dataset_id
        ) THEN
            RAISE EXCEPTION 'proposal file object must belong to the proposal dataset'
                USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END;
    $$ LANGUAGE plpgsql
    """,
    """
    CREATE TRIGGER ordp_proposal_file_dataset_guard
    BEFORE INSERT OR UPDATE ON datasets_datasetproposalfile
    FOR EACH ROW EXECUTE FUNCTION ordp_validate_proposal_file_links()
    """,
    """
    CREATE FUNCTION ordp_validate_legacy_file_dataset_link() RETURNS trigger AS $$
    BEGIN
        IF NEW.stored_object_id IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM datasets_storedobject
            WHERE id = NEW.stored_object_id AND dataset_id = NEW.dataset_id
        ) THEN
            RAISE EXCEPTION 'legacy file object must belong to the same dataset'
                USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END;
    $$ LANGUAGE plpgsql
    """,
    """
    CREATE TRIGGER ordp_legacy_file_dataset_guard
    BEFORE INSERT OR UPDATE OF dataset_id, stored_object_id ON datasets_datasetfile
    FOR EACH ROW EXECUTE FUNCTION ordp_validate_legacy_file_dataset_link()
    """,
    """
    CREATE FUNCTION ordp_validate_attribution_dataset_links() RETURNS trigger AS $$
    BEGIN
        IF NOT EXISTS (
            SELECT 1 FROM datasets_datasetrelease
            WHERE id = NEW.release_id AND dataset_id = NEW.dataset_id
        ) THEN
            RAISE EXCEPTION 'attribution release must belong to the same dataset'
                USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END;
    $$ LANGUAGE plpgsql
    """,
    """
    CREATE TRIGGER ordp_attribution_dataset_guard
    BEFORE INSERT OR UPDATE OF dataset_id, release_id ON datasets_contributorattribution
    FOR EACH ROW EXECUTE FUNCTION ordp_validate_attribution_dataset_links()
    """,
    """
    CREATE FUNCTION ordp_immutable_stored_object() RETURNS trigger AS $$
    BEGIN
        IF TG_OP = 'INSERT' THEN
            IF NEW.file_size < 0 OR NEW.checksum_sha256 !~ '^[0-9a-f]{64}$' THEN
                RAISE EXCEPTION 'stored object size or SHA-256 checksum is invalid'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END IF;
        IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'stored objects are immutable and cannot be deleted'
                USING ERRCODE = '23514';
        END IF;
        IF (to_jsonb(NEW) - ARRAY['verified_at', 'uploaded_by_id'])
             <> (to_jsonb(OLD) - ARRAY['verified_at', 'uploaded_by_id'])
           OR (NEW.uploaded_by_id IS NOT NULL AND NEW.uploaded_by_id IS DISTINCT FROM OLD.uploaded_by_id) THEN
            RAISE EXCEPTION 'stored object identity and content metadata are immutable'
                USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END;
    $$ LANGUAGE plpgsql
    """,
    """
    CREATE TRIGGER ordp_stored_object_immutable
    BEFORE INSERT OR UPDATE OR DELETE ON datasets_storedobject
    FOR EACH ROW EXECUTE FUNCTION ordp_immutable_stored_object()
    """,
    """
    CREATE FUNCTION ordp_immutable_release() RETURNS trigger AS $$
    BEGIN
        IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'dataset releases are immutable and cannot be deleted'
                USING ERRCODE = '23514';
        END IF;
        IF OLD.status = 'draft' AND NEW.status = 'published'
           AND (to_jsonb(NEW) - 'status') = (to_jsonb(OLD) - 'status') THEN
            IF NOT EXISTS (
                SELECT 1 FROM datasets_datasetreleasefile WHERE release_id = NEW.id
            ) THEN
                RAISE EXCEPTION 'a published release must contain at least one file'
                    USING ERRCODE = '23514';
            END IF;
            IF jsonb_typeof(NEW.metadata_snapshot) IS DISTINCT FROM 'object'
               OR (NEW.metadata_snapshot ->> 'schema_version')::integer
                    IS DISTINCT FROM NEW.snapshot_schema_version THEN
                RAISE EXCEPTION 'release metadata snapshot schema is inconsistent'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END IF;
        IF OLD.status = 'published' AND NEW.status = 'withdrawn'
           AND NEW.withdrawn_at IS NOT NULL
           AND btrim(NEW.withdrawal_reason) <> ''
           AND (to_jsonb(NEW) - ARRAY['status', 'withdrawn_at', 'withdrawn_by_id', 'withdrawal_reason'])
             = (to_jsonb(OLD) - ARRAY['status', 'withdrawn_at', 'withdrawn_by_id', 'withdrawal_reason']) THEN
            RETURN NEW;
        END IF;
        IF OLD.status = NEW.status
           AND (to_jsonb(NEW) - ARRAY['created_by_id', 'published_by_id', 'withdrawn_by_id'])
             = (to_jsonb(OLD) - ARRAY['created_by_id', 'published_by_id', 'withdrawn_by_id'])
           AND (NEW.created_by_id IS NULL OR NEW.created_by_id IS NOT DISTINCT FROM OLD.created_by_id)
           AND (NEW.published_by_id IS NULL OR NEW.published_by_id IS NOT DISTINCT FROM OLD.published_by_id)
           AND (NEW.withdrawn_by_id IS NULL OR NEW.withdrawn_by_id IS NOT DISTINCT FROM OLD.withdrawn_by_id) THEN
            RETURN NEW;
        END IF;
        RAISE EXCEPTION 'dataset release snapshots are immutable'
            USING ERRCODE = '23514';
    END;
    $$ LANGUAGE plpgsql
    """,
    """
    CREATE TRIGGER ordp_release_immutable
    BEFORE UPDATE OR DELETE ON datasets_datasetrelease
    FOR EACH ROW EXECUTE FUNCTION ordp_immutable_release()
    """,
]


def install_postgresql_integrity_triggers(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        for statement in POSTGRESQL_FUNCTIONS_AND_TRIGGERS:
            cursor.execute(statement)


def remove_postgresql_integrity_triggers(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    statements = [
        "DROP TRIGGER IF EXISTS ordp_release_immutable ON datasets_datasetrelease",
        "DROP TRIGGER IF EXISTS ordp_stored_object_immutable ON datasets_storedobject",
        "DROP TRIGGER IF EXISTS ordp_attribution_dataset_guard ON datasets_contributorattribution",
        "DROP TRIGGER IF EXISTS ordp_proposal_file_dataset_guard ON datasets_datasetproposalfile",
        "DROP TRIGGER IF EXISTS ordp_legacy_file_dataset_guard ON datasets_datasetfile",
        "DROP TRIGGER IF EXISTS ordp_release_file_immutable ON datasets_datasetreleasefile",
        "DROP TRIGGER IF EXISTS ordp_proposal_dataset_guard ON datasets_datasetproposal",
        "DROP TRIGGER IF EXISTS ordp_release_dataset_guard ON datasets_datasetrelease",
        "DROP TRIGGER IF EXISTS ordp_dataset_release_pointer_guard ON datasets_dataset",
        "DROP FUNCTION IF EXISTS ordp_immutable_release()",
        "DROP FUNCTION IF EXISTS ordp_immutable_stored_object()",
        "DROP FUNCTION IF EXISTS ordp_validate_attribution_dataset_links()",
        "DROP FUNCTION IF EXISTS ordp_validate_proposal_file_links()",
        "DROP FUNCTION IF EXISTS ordp_validate_legacy_file_dataset_link()",
        "DROP FUNCTION IF EXISTS ordp_validate_release_file_links()",
        "DROP FUNCTION IF EXISTS ordp_validate_proposal_dataset_links()",
        "DROP FUNCTION IF EXISTS ordp_validate_release_dataset_links()",
        "DROP FUNCTION IF EXISTS ordp_validate_dataset_release_pointer()",
    ]
    with schema_editor.connection.cursor() as cursor:
        for statement in statements:
            cursor.execute(statement)


class Migration(migrations.Migration):

    dependencies = [
        ("datasets", "0027_datasetfile_stored_object"),
    ]

    operations = [
        migrations.AlterField(
            model_name="datasetrelease",
            name="status",
            field=models.CharField(
                choices=[
                    ("draft", "Draft"),
                    ("published", "Published"),
                    ("withdrawn", "Withdrawn"),
                ],
                default="draft",
                max_length=16,
            ),
        ),
        migrations.AddConstraint(
            model_name="storedobject",
            constraint=models.CheckConstraint(
                condition=models.Q(("file_size__gte", 0)),
                name="stored_object_nonnegative_size",
            ),
        ),
        migrations.AddConstraint(
            model_name="datasetproposalfile",
            constraint=models.UniqueConstraint(
                fields=("proposal", "stored_object"),
                name="unique_object_per_proposal",
            ),
        ),
        migrations.RunPython(
            install_postgresql_integrity_triggers,
            remove_postgresql_integrity_triggers,
        ),
    ]
