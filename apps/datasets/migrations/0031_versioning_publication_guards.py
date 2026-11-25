from django.db import migrations


POSTGRESQL_RELEASE_FILE_GUARD = r"""
CREATE OR REPLACE FUNCTION ordp_validate_release_file_links() RETURNS trigger AS $$
DECLARE
    release_dataset uuid;
    release_status varchar(16);
BEGIN
    IF TG_OP = 'DELETE' THEN
        SELECT status INTO release_status
          FROM datasets_datasetrelease
         WHERE id = OLD.release_id
         FOR UPDATE;
        IF release_status = 'draft' THEN
            RETURN OLD;
        END IF;
        RAISE EXCEPTION 'published release manifests are immutable'
            USING ERRCODE = '23514';
    END IF;

    IF TG_OP = 'UPDATE' THEN
        IF NEW.id IS DISTINCT FROM OLD.id
           OR NEW.release_id IS DISTINCT FROM OLD.release_id
           OR NEW.logical_file_id IS DISTINCT FROM OLD.logical_file_id THEN
            RAISE EXCEPTION 'release file identity cannot be changed'
                USING ERRCODE = '23514';
        END IF;
        SELECT dataset_id, status INTO release_dataset, release_status
          FROM datasets_datasetrelease
         WHERE id = OLD.release_id
         FOR UPDATE;
        IF release_status IS DISTINCT FROM 'draft' THEN
            RAISE EXCEPTION 'published release manifests are immutable'
                USING ERRCODE = '23514';
        END IF;
    END IF;

    SELECT dataset_id, status INTO release_dataset, release_status
      FROM datasets_datasetrelease
     WHERE id = NEW.release_id
     FOR UPDATE;
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
"""

POSTGRESQL_RELEASE_GUARD = r"""
CREATE OR REPLACE FUNCTION ordp_immutable_release() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.status IS DISTINCT FROM 'draft' THEN
            RAISE EXCEPTION 'dataset releases must be created in draft status'
                USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END IF;
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
           OR (NEW.metadata_snapshot ->> 'schema_version') IS DISTINCT FROM NEW.snapshot_schema_version::text
           OR jsonb_typeof(NEW.citation_snapshot) IS DISTINCT FROM 'object'
           OR jsonb_typeof(NEW.license_snapshot) IS DISTINCT FROM 'object' THEN
            RAISE EXCEPTION 'release snapshots are incomplete or inconsistent'
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
"""

POSTGRESQL_POINTER_GUARD = r"""
CREATE OR REPLACE FUNCTION ordp_validate_dataset_release_pointer() RETURNS trigger AS $$
DECLARE
    release_dataset uuid;
    release_status varchar(16);
    release_schema integer;
    release_metadata jsonb;
    release_citation jsonb;
    release_license jsonb;
BEGIN
    IF NEW.published_release_id IS NULL THEN RETURN NEW; END IF;
    SELECT dataset_id, status, snapshot_schema_version, metadata_snapshot,
           citation_snapshot, license_snapshot
      INTO release_dataset, release_status, release_schema, release_metadata,
           release_citation, release_license
      FROM datasets_datasetrelease
     WHERE id = NEW.published_release_id
     FOR UPDATE;
    IF release_dataset IS DISTINCT FROM NEW.id
       OR release_status IS DISTINCT FROM 'published'
       OR jsonb_typeof(release_metadata) IS DISTINCT FROM 'object'
       OR (release_metadata ->> 'schema_version') IS DISTINCT FROM release_schema::text
       OR jsonb_typeof(release_citation) IS DISTINCT FROM 'object'
       OR jsonb_typeof(release_license) IS DISTINCT FROM 'object'
       OR NOT EXISTS (
           SELECT 1 FROM datasets_datasetreleasefile
            WHERE release_id = NEW.published_release_id
       ) THEN
        RAISE EXCEPTION 'published release pointer must reference a complete release for this dataset'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql
"""

POSTGRESQL_PROPOSAL_GUARD = r"""
CREATE OR REPLACE FUNCTION ordp_guard_dataset_proposal_lifecycle() RETURNS trigger AS $$
DECLARE
    reviewer_count integer;
    approve_count integer;
    reject_count integer;
    vote_quorum integer;
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.status IS DISTINCT FROM 'draft' OR NEW.resulting_release_id IS NOT NULL THEN
            RAISE EXCEPTION 'proposals must be created as drafts without a resulting release'
                USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END IF;
    IF NEW.status IS NOT DISTINCT FROM OLD.status THEN
        RETURN NEW;
    END IF;

    IF OLD.status = 'draft' AND NEW.status IN ('pending_review', 'cancelled') THEN
        IF NEW.status = 'pending_review'
           AND (NEW.submitted_at IS NULL OR NEW.computed_min_change_type IS NULL
                OR NEW.computed_min_change_type NOT IN ('patch', 'minor', 'major')) THEN
            RAISE EXCEPTION 'submitted proposals require a valid computed minimum change type'
                USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END IF;
    IF OLD.status = 'draft' AND NEW.status = 'stale' THEN
        RETURN NEW;
    END IF;

    IF OLD.status = 'pending_review'
       AND NEW.status IN ('approved', 'rejected', 'stale', 'cancelled') THEN
        IF NEW.status IN ('approved', 'rejected') THEN
            SELECT COUNT(DISTINCT p.id) INTO reviewer_count
              FROM accounts_userprofile p
              JOIN accounts_userrole r ON r.profile_id = p.id AND r.role = 'reviewer';
            vote_quorum := CASE WHEN reviewer_count = 0 THEN 1 ELSE LEAST(3, reviewer_count) END;
            SELECT COUNT(*) FILTER (WHERE v.vote = 'approve'),
                   COUNT(*) FILTER (WHERE v.vote = 'reject')
              INTO approve_count, reject_count
              FROM datasets_datasetproposalvote v
              JOIN accounts_userprofile p ON p.user_id = v.reviewer_id
              JOIN accounts_userrole r ON r.profile_id = p.id AND r.role = 'reviewer'
             WHERE v.proposal_id = OLD.id;
            IF approve_count + reject_count < vote_quorum
               OR (NEW.status = 'approved' AND approve_count <= reject_count)
               OR (NEW.status = 'rejected' AND reject_count <= approve_count) THEN
                RAISE EXCEPTION 'proposal decision does not satisfy reviewer quorum and majority'
                    USING ERRCODE = '23514';
            END IF;
        END IF;
        RETURN NEW;
    END IF;

    IF OLD.status = 'approved' AND NEW.status IN ('published', 'stale', 'cancelled') THEN
        IF NEW.status = 'published' AND (
            NEW.resulting_release_id IS NULL OR NEW.published_at IS NULL
            OR NEW.final_change_type IS NULL
            OR NEW.final_change_type NOT IN ('patch', 'minor', 'major')
            OR NOT EXISTS (
                SELECT 1 FROM datasets_datasetrelease r
                 WHERE r.id = NEW.resulting_release_id
                   AND r.dataset_id = NEW.dataset_id
                   AND r.status = 'published'
            )
        ) THEN
            RAISE EXCEPTION 'a proposal can be published only with its published resulting release'
                USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END IF;

    RAISE EXCEPTION 'invalid dataset proposal state transition'
        USING ERRCODE = '23514';
END;
$$ LANGUAGE plpgsql
"""

POSTGRESQL_REVERSE = r"""
DROP TRIGGER IF EXISTS ordp_proposal_lifecycle_guard ON datasets_datasetproposal;
DROP FUNCTION IF EXISTS ordp_guard_dataset_proposal_lifecycle();

CREATE OR REPLACE FUNCTION ordp_validate_dataset_release_pointer() RETURNS trigger AS $$
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
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION ordp_validate_release_file_links() RETURNS trigger AS $$
DECLARE
    release_dataset uuid;
    release_status varchar(16);
BEGIN
    IF TG_OP = 'DELETE' THEN
        SELECT status INTO release_status FROM datasets_datasetrelease WHERE id = OLD.release_id;
        IF release_status = 'draft' THEN RETURN OLD; END IF;
        RAISE EXCEPTION 'published release manifests are immutable' USING ERRCODE = '23514';
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
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION ordp_immutable_release() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'dataset releases are immutable and cannot be deleted' USING ERRCODE = '23514';
    END IF;
    IF OLD.status = 'draft' AND NEW.status = 'published'
       AND (to_jsonb(NEW) - 'status') = (to_jsonb(OLD) - 'status') THEN
        IF NOT EXISTS (SELECT 1 FROM datasets_datasetreleasefile WHERE release_id = NEW.id) THEN
            RAISE EXCEPTION 'a published release must contain at least one file' USING ERRCODE = '23514';
        END IF;
        IF jsonb_typeof(NEW.metadata_snapshot) IS DISTINCT FROM 'object'
           OR (NEW.metadata_snapshot ->> 'schema_version')::integer IS DISTINCT FROM NEW.snapshot_schema_version THEN
            RAISE EXCEPTION 'release metadata snapshot schema is inconsistent' USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END IF;
    IF OLD.status = 'published' AND NEW.status = 'withdrawn' AND NEW.withdrawn_at IS NOT NULL
       AND btrim(NEW.withdrawal_reason) <> ''
       AND (to_jsonb(NEW) - ARRAY['status','withdrawn_at','withdrawn_by_id','withdrawal_reason'])
         = (to_jsonb(OLD) - ARRAY['status','withdrawn_at','withdrawn_by_id','withdrawal_reason']) THEN
        RETURN NEW;
    END IF;
    IF OLD.status = NEW.status
       AND (to_jsonb(NEW) - ARRAY['created_by_id','published_by_id','withdrawn_by_id'])
         = (to_jsonb(OLD) - ARRAY['created_by_id','published_by_id','withdrawn_by_id'])
       AND (NEW.created_by_id IS NULL OR NEW.created_by_id IS NOT DISTINCT FROM OLD.created_by_id)
       AND (NEW.published_by_id IS NULL OR NEW.published_by_id IS NOT DISTINCT FROM OLD.published_by_id)
       AND (NEW.withdrawn_by_id IS NULL OR NEW.withdrawn_by_id IS NOT DISTINCT FROM OLD.withdrawn_by_id) THEN
        RETURN NEW;
    END IF;
    RAISE EXCEPTION 'dataset release snapshots are immutable' USING ERRCODE = '23514';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS ordp_release_immutable ON datasets_datasetrelease;
CREATE TRIGGER ordp_release_immutable BEFORE UPDATE OR DELETE ON datasets_datasetrelease
FOR EACH ROW EXECUTE FUNCTION ordp_immutable_release();
"""


def install_guards(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(POSTGRESQL_POINTER_GUARD)
        cursor.execute(POSTGRESQL_RELEASE_FILE_GUARD)
        cursor.execute(POSTGRESQL_RELEASE_GUARD)
        cursor.execute(POSTGRESQL_PROPOSAL_GUARD)
        cursor.execute(
            "DROP TRIGGER IF EXISTS ordp_release_immutable ON datasets_datasetrelease"
        )
        cursor.execute(
            """
            CREATE TRIGGER ordp_release_immutable
            BEFORE INSERT OR UPDATE OR DELETE ON datasets_datasetrelease
            FOR EACH ROW EXECUTE FUNCTION ordp_immutable_release()
            """
        )
        cursor.execute(
            "DROP TRIGGER IF EXISTS ordp_proposal_lifecycle_guard ON datasets_datasetproposal"
        )
        cursor.execute(
            """
            CREATE TRIGGER ordp_proposal_lifecycle_guard
            BEFORE INSERT OR UPDATE ON datasets_datasetproposal
            FOR EACH ROW EXECUTE FUNCTION ordp_guard_dataset_proposal_lifecycle()
            """
        )


def reverse_guards(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(POSTGRESQL_REVERSE)


class Migration(migrations.Migration):
    dependencies = [("datasets", "0030_proposal_content_immutability")]

    operations = [
        migrations.RunPython(install_guards, reverse_guards),
    ]
