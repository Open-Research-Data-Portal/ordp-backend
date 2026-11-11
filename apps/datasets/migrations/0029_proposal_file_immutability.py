from django.db import migrations


POSTGRESQL_INSTALL = """
CREATE OR REPLACE FUNCTION ordp_validate_proposal_file_links() RETURNS trigger AS $$
DECLARE
    proposal_id_to_check uuid;
    proposal_status varchar(16);
BEGIN
    IF TG_OP = 'DELETE' THEN
        proposal_id_to_check := OLD.proposal_id;
    ELSE
        proposal_id_to_check := NEW.proposal_id;
    END IF;

    IF TG_OP = 'UPDATE' AND NEW.proposal_id IS DISTINCT FROM OLD.proposal_id THEN
        RAISE EXCEPTION 'proposal files cannot be moved between proposals'
            USING ERRCODE = '23514';
    END IF;

    SELECT status INTO proposal_status
    FROM datasets_datasetproposal
    WHERE id = proposal_id_to_check
    FOR UPDATE;

    IF NOT FOUND AND TG_OP = 'DELETE' AND pg_trigger_depth() > 1 THEN
        RETURN OLD;
    END IF;
    IF proposal_status IS DISTINCT FROM 'draft' THEN
        RAISE EXCEPTION 'only draft proposal manifests can be edited'
            USING ERRCODE = '23514';
    END IF;

    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
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
"""

POSTGRESQL_PROPOSAL_DELETE_GUARD = """
CREATE OR REPLACE FUNCTION ordp_prevent_non_draft_proposal_delete() RETURNS trigger AS $$
BEGIN
    IF OLD.status IS DISTINCT FROM 'draft' THEN
        RAISE EXCEPTION 'only draft proposals can be deleted'
            USING ERRCODE = '23514';
    END IF;
    RETURN OLD;
END;
$$ LANGUAGE plpgsql
"""

POSTGRESQL_REMOVE = [
    "DROP TRIGGER IF EXISTS ordp_proposal_file_dataset_guard ON datasets_datasetproposalfile",
    "DROP TRIGGER IF EXISTS ordp_proposal_delete_guard ON datasets_datasetproposal",
    "DROP FUNCTION IF EXISTS ordp_prevent_non_draft_proposal_delete()",
    "DROP FUNCTION IF EXISTS ordp_validate_proposal_file_links()",
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
]


def install_postgresql_trigger(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(POSTGRESQL_INSTALL)
        cursor.execute(POSTGRESQL_PROPOSAL_DELETE_GUARD)
        cursor.execute(
            "DROP TRIGGER IF EXISTS ordp_proposal_file_dataset_guard "
            "ON datasets_datasetproposalfile"
        )
        cursor.execute(
            """
            CREATE TRIGGER ordp_proposal_file_dataset_guard
            BEFORE INSERT OR UPDATE OR DELETE ON datasets_datasetproposalfile
            FOR EACH ROW EXECUTE FUNCTION ordp_validate_proposal_file_links()
            """
        )
        cursor.execute(
            "DROP TRIGGER IF EXISTS ordp_proposal_delete_guard ON datasets_datasetproposal"
        )
        cursor.execute(
            """
            CREATE TRIGGER ordp_proposal_delete_guard
            BEFORE DELETE ON datasets_datasetproposal
            FOR EACH ROW EXECUTE FUNCTION ordp_prevent_non_draft_proposal_delete()
            """
        )


def remove_postgresql_trigger(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        for statement in POSTGRESQL_REMOVE:
            cursor.execute(statement)


class Migration(migrations.Migration):
    dependencies = [("datasets", "0028_release_integrity_constraints")]

    operations = [
        migrations.RunPython(install_postgresql_trigger, remove_postgresql_trigger),
    ]
