from django.db import migrations


FORWARD_SQL = r"""
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

    -- Same-state updates are permitted only for the activity timestamp.
    -- Proposal content, submission/decision/publication fields, and identifiers
    -- remain protected by this row-level guard.
    -- Draft proposals remain editable.
-- Only prevent draft proposals from acquiring lifecycle/publication
-- fields before they are submitted.
IF NEW.status IS NOT DISTINCT FROM OLD.status THEN
    IF OLD.status = 'draft' THEN
        IF NEW.resulting_release_id IS DISTINCT FROM OLD.resulting_release_id
           OR NEW.final_change_type IS DISTINCT FROM OLD.final_change_type
           OR NEW.decided_at IS DISTINCT FROM OLD.decided_at
           OR NEW.published_at IS DISTINCT FROM OLD.published_at THEN
            RAISE EXCEPTION 'draft proposals cannot acquire decision or publication fields'
                USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END IF;

    -- Submitted proposals may only update review activity.
    IF (to_jsonb(NEW) - ARRAY['status', 'last_activity_at'])
       = (to_jsonb(OLD) - ARRAY['status', 'last_activity_at']) THEN
        RETURN NEW;
    END IF;

    RAISE EXCEPTION 'submitted proposal fields are immutable except review activity'
        USING ERRCODE = '23514';
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
$$ LANGUAGE plpgsql;
"""

REVERSE_SQL = "\nCREATE OR REPLACE FUNCTION ordp_guard_dataset_proposal_lifecycle() RETURNS trigger AS $$\nDECLARE\n    reviewer_count integer;\n    approve_count integer;\n    reject_count integer;\n    vote_quorum integer;\nBEGIN\n    IF TG_OP = 'INSERT' THEN\n        IF NEW.status IS DISTINCT FROM 'draft' OR NEW.resulting_release_id IS NOT NULL THEN\n            RAISE EXCEPTION 'proposals must be created as drafts without a resulting release'\n                USING ERRCODE = '23514';\n        END IF;\n        RETURN NEW;\n    END IF;\n\n    -- If status unchanged, ensure no protected fields are modified\n    IF NEW.status IS NOT DISTINCT FROM OLD.status THEN\n        IF (to_jsonb(NEW) - 'status') <> (to_jsonb(OLD) - 'status') THEN\n            RAISE EXCEPTION 'submitted proposal content is immutable after submission'\n                USING ERRCODE = '23514';\n        END IF;\n        RETURN NEW;\n    END IF;\n\n    IF OLD.status = 'draft' AND NEW.status IN ('pending_review', 'cancelled') THEN\n        IF NEW.status = 'pending_review'\n           AND (NEW.submitted_at IS NULL OR NEW.computed_min_change_type IS NULL\n                OR NEW.computed_min_change_type NOT IN ('patch', 'minor', 'major')) THEN\n            RAISE EXCEPTION 'submitted proposals require a valid computed minimum change type'\n                USING ERRCODE = '23514';\n        END IF;\n        RETURN NEW;\n    END IF;\n    IF OLD.status = 'draft' AND NEW.status = 'stale' THEN\n        RETURN NEW;\n    END IF;\n\n    IF OLD.status = 'pending_review'\n       AND NEW.status IN ('approved', 'rejected', 'stale', 'cancelled') THEN\n        IF NEW.status IN ('approved', 'rejected') THEN\n            SELECT COUNT(DISTINCT p.id) INTO reviewer_count\n              FROM accounts_userprofile p\n              JOIN accounts_userrole r ON r.profile_id = p.id AND r.role = 'reviewer';\n            vote_quorum := CASE WHEN reviewer_count = 0 THEN 1 ELSE LEAST(3, reviewer_count) END;\n            SELECT COUNT(*) FILTER (WHERE v.vote = 'approve'),\n                   COUNT(*) FILTER (WHERE v.vote = 'reject')\n              INTO approve_count, reject_count\n              FROM datasets_datasetproposalvote v\n              JOIN accounts_userprofile p ON p.user_id = v.reviewer_id\n              JOIN accounts_userrole r ON r.profile_id = p.id AND r.role = 'reviewer'\n             WHERE v.proposal_id = OLD.id;\n            IF approve_count + reject_count < vote_quorum\n               OR (NEW.status = 'approved' AND approve_count <= reject_count)\n               OR (NEW.status = 'rejected' AND reject_count <= approve_count) THEN\n                RAISE EXCEPTION 'proposal decision does not satisfy reviewer quorum and majority'\n                    USING ERRCODE = '23514';\n            END IF;\n        END IF;\n        RETURN NEW;\n    END IF;\n\n    IF OLD.status = 'approved' AND NEW.status IN ('published', 'stale', 'cancelled') THEN\n        IF NEW.status = 'published' AND (\n            NEW.resulting_release_id IS NULL OR NEW.published_at IS NULL\n            OR NEW.final_change_type IS NULL\n            OR NEW.final_change_type NOT IN ('patch', 'minor', 'major')\n            OR NOT EXISTS (\n                SELECT 1 FROM datasets_datasetrelease r\n                 WHERE r.id = NEW.resulting_release_id\n                   AND r.dataset_id = NEW.dataset_id\n                   AND r.status = 'published'\n            )\n        ) THEN\n            RAISE EXCEPTION 'a proposal can be published only with its published resulting release'\n                USING ERRCODE = '23514';\n        END IF;\n        RETURN NEW;\n    END IF;\n\n    RAISE EXCEPTION 'invalid dataset proposal state transition'\n        USING ERRCODE = '23514';\nEND;\n$$ LANGUAGE plpgsql;\n"


def install_trigger(apps, schema_editor):
    if schema_editor.connection.vendor != 'postgresql':
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(FORWARD_SQL)

def remove_trigger(apps, schema_editor):
    if schema_editor.connection.vendor != 'postgresql':
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(REVERSE_SQL)

class Migration(migrations.Migration):
    dependencies = [
        ('datasets', '0034_proposal_lifecycle_protection'),
    ]
    operations = [
        migrations.RunPython(install_trigger, remove_trigger),
    ]