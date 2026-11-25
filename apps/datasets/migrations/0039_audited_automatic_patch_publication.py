
from django.db import migrations


FORWARD_SQL = r"""
CREATE OR REPLACE FUNCTION ordp_guard_dataset_proposal_lifecycle()
RETURNS trigger AS $$
DECLARE
    reviewer_count integer;
    approve_count integer;
    reject_count integer;
    vote_quorum integer;
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.status IS DISTINCT FROM 'draft'
           OR NEW.resulting_release_id IS NOT NULL THEN
            RAISE EXCEPTION 'proposals must be created as drafts without a resulting release'
                USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END IF;

    IF NEW.status IS NOT DISTINCT FROM OLD.status THEN

        -- Draft content is editable, but lifecycle fields remain protected.
        IF OLD.status = 'draft' THEN
            IF NEW.resulting_release_id IS DISTINCT FROM OLD.resulting_release_id
               OR NEW.final_change_type IS DISTINCT FROM OLD.final_change_type
               OR NEW.decided_at IS DISTINCT FROM OLD.decided_at
               OR NEW.published_at IS DISTINCT FROM OLD.published_at
               OR NEW.review_bypassed_at IS DISTINCT FROM OLD.review_bypassed_at
               OR NEW.review_bypassed_by_id IS DISTINCT FROM OLD.review_bypassed_by_id
               OR NEW.review_bypass_reason IS DISTINCT FROM OLD.review_bypass_reason
            THEN
                RAISE EXCEPTION 'draft proposals cannot acquire decision or publication fields'
                    USING ERRCODE = '23514';
            END IF;

            RETURN NEW;
        END IF;

        -- Submitted proposals are immutable except for review activity.
        IF (to_jsonb(NEW) - ARRAY['status', 'last_activity_at'])
           <> (to_jsonb(OLD) - ARRAY['status', 'last_activity_at']) THEN
            RAISE EXCEPTION 'submitted proposal content is immutable after submission'
                USING ERRCODE = '23514';
        END IF;

        RETURN NEW;
    END IF;

    IF OLD.status = 'draft' AND NEW.status = 'pending_review' THEN
        IF NEW.submitted_at IS NULL
           OR NEW.computed_min_change_type IS NULL
           OR NEW.computed_min_change_type NOT IN ('patch', 'minor', 'major') THEN
            RAISE EXCEPTION 'submitted proposals require a valid computed minimum change type'
                USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END IF;

    IF OLD.status = 'draft' AND NEW.status = 'approved' THEN
        IF NEW.submitted_at IS NULL
           OR NEW.decided_at IS NULL
           OR NEW.computed_min_change_type IS DISTINCT FROM 'patch'
           OR NEW.review_bypassed_at IS NULL
           OR NEW.review_bypassed_by_id IS NULL
           OR NEW.review_bypass_reason <> 'formatting_only_descriptive_patch'
           OR EXISTS (
               SELECT 1
               FROM datasets_datasetproposalvote v
               WHERE v.proposal_id = OLD.id
           ) THEN
            RAISE EXCEPTION 'automatic approval is restricted to audited formatting-only PATCH proposals'
                USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END IF;

    IF OLD.status = 'draft'
       AND NEW.status IN ('cancelled', 'stale') THEN
        RETURN NEW;
    END IF;

    IF OLD.status = 'pending_review'
       AND NEW.status IN ('approved', 'rejected', 'stale', 'cancelled') THEN

        IF NEW.status IN ('approved', 'rejected') THEN
            SELECT COUNT(DISTINCT p.id)
            INTO reviewer_count
            FROM accounts_userprofile p
            JOIN accounts_userrole r
              ON r.profile_id = p.id AND r.role = 'reviewer';

            vote_quorum := CASE
                WHEN reviewer_count = 0 THEN 1
                ELSE LEAST(3, reviewer_count)
            END;

            SELECT
                COUNT(*) FILTER (WHERE v.vote = 'approve'),
                COUNT(*) FILTER (WHERE v.vote = 'reject')
            INTO approve_count, reject_count
            FROM datasets_datasetproposalvote v
            JOIN accounts_userprofile p ON p.user_id = v.reviewer_id
            JOIN accounts_userrole r
              ON r.profile_id = p.id AND r.role = 'reviewer'
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

    IF OLD.status = 'approved'
       AND NEW.status IN ('published', 'stale', 'cancelled') THEN

        IF NEW.status = 'published' AND (
            NEW.resulting_release_id IS NULL
            OR NEW.published_at IS NULL
            OR NEW.final_change_type IS NULL
            OR NEW.final_change_type NOT IN ('patch', 'minor', 'major')
            OR NOT EXISTS (
                SELECT 1
                FROM datasets_datasetrelease r
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


def install_trigger(apps, schema_editor):
    if schema_editor.connection.vendor != 'postgresql':
        return
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(FORWARD_SQL)

class Migration(migrations.Migration):
    dependencies = [
        ('datasets', '0038_uploadsession_proposal'),
    ]
    operations = [
        migrations.RunPython(install_trigger, migrations.RunPython.noop),
    ]