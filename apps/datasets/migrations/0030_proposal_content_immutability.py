from django.db import migrations


POSTGRESQL_INSTALL = """
CREATE FUNCTION ordp_prevent_submitted_proposal_content_update() RETURNS trigger AS $$
BEGIN
    IF OLD.status <> 'draft' AND NEW.status = 'draft' THEN
        RAISE EXCEPTION 'a submitted proposal cannot return to draft'
            USING ERRCODE = '23514';
    END IF;
    IF OLD.status <> 'draft' AND (
        NEW.dataset_id IS DISTINCT FROM OLD.dataset_id OR
        NEW.base_release_id IS DISTINCT FROM OLD.base_release_id OR
        NEW.source IS DISTINCT FROM OLD.source OR
        NEW.proposed_metadata IS DISTINCT FROM OLD.proposed_metadata OR
        NEW.proposed_license IS DISTINCT FROM OLD.proposed_license OR
        NEW.license_change_reason IS DISTINCT FROM OLD.license_change_reason OR
        NEW.declared_change_type IS DISTINCT FROM OLD.declared_change_type OR
        NEW.computed_min_change_type IS DISTINCT FROM OLD.computed_min_change_type OR
        NEW.change_summary IS DISTINCT FROM OLD.change_summary OR
        NEW.diff_percentage IS DISTINCT FROM OLD.diff_percentage OR
        NEW.ai_summary IS DISTINCT FROM OLD.ai_summary OR
        NEW.submitted_by_id IS DISTINCT FROM OLD.submitted_by_id
    ) THEN
        RAISE EXCEPTION 'submitted proposal content is immutable'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql
"""


def install_postgresql_trigger(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(POSTGRESQL_INSTALL)
        cursor.execute(
            """
            CREATE TRIGGER ordp_proposal_content_immutable
            BEFORE UPDATE ON datasets_datasetproposal
            FOR EACH ROW EXECUTE FUNCTION ordp_prevent_submitted_proposal_content_update()
            """
        )


def remove_postgresql_trigger(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(
            "DROP TRIGGER IF EXISTS ordp_proposal_content_immutable "
            "ON datasets_datasetproposal"
        )
        cursor.execute(
            "DROP FUNCTION IF EXISTS ordp_prevent_submitted_proposal_content_update()"
        )


class Migration(migrations.Migration):
    dependencies = [("datasets", "0029_proposal_file_immutability")]

    operations = [
        migrations.RunPython(install_postgresql_trigger, remove_postgresql_trigger),
    ]
