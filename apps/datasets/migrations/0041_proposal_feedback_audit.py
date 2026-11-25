import uuid
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


LIFECYCLE_SQL = r"""
CREATE OR REPLACE FUNCTION ordp_guard_dataset_proposal_lifecycle() RETURNS trigger AS $$
DECLARE
    reviewer_count integer;
    approve_count integer;
    reject_count integer;
    vote_quorum integer;
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.status IS DISTINCT FROM 'draft' OR NEW.resulting_release_id IS NOT NULL THEN
            RAISE EXCEPTION 'proposals must be created as drafts without a resulting release' USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END IF;

    IF NEW.status IS NOT DISTINCT FROM OLD.status THEN
        IF OLD.status = 'draft' THEN
            IF NEW.resulting_release_id IS DISTINCT FROM OLD.resulting_release_id
               OR NEW.final_change_type IS DISTINCT FROM OLD.final_change_type
               OR NEW.decided_at IS DISTINCT FROM OLD.decided_at
               OR NEW.published_at IS DISTINCT FROM OLD.published_at
               OR NEW.review_bypassed_at IS DISTINCT FROM OLD.review_bypassed_at
               OR NEW.review_bypassed_by_id IS DISTINCT FROM OLD.review_bypassed_by_id
               OR NEW.review_bypass_reason IS DISTINCT FROM OLD.review_bypass_reason THEN
                RAISE EXCEPTION 'draft proposals cannot acquire decision or publication fields' USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END IF;
        IF (to_jsonb(NEW) - ARRAY['status', 'last_activity_at'])
           <> (to_jsonb(OLD) - ARRAY['status', 'last_activity_at']) THEN
            RAISE EXCEPTION 'submitted proposal content is immutable after submission' USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END IF;

    IF OLD.status = 'draft' AND NEW.status = 'pending_review' THEN
        IF NEW.submitted_at IS NULL OR NEW.computed_min_change_type IS NULL
           OR NEW.computed_min_change_type NOT IN ('patch', 'minor', 'major') THEN
            RAISE EXCEPTION 'submitted proposals require a valid computed minimum change type' USING ERRCODE = '23514';
        END IF;
        IF (to_jsonb(NEW) - ARRAY['status', 'submitted_at', 'computed_min_change_type', 'last_activity_at'])
           <> (to_jsonb(OLD) - ARRAY['status', 'submitted_at', 'computed_min_change_type', 'last_activity_at']) THEN
            RAISE EXCEPTION 'proposal content cannot change during submission' USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END IF;

    IF OLD.status = 'draft' AND NEW.status = 'approved' THEN
        IF NEW.submitted_at IS NULL OR NEW.decided_at IS NULL
           OR NEW.computed_min_change_type IS DISTINCT FROM 'patch'
           OR NEW.review_bypassed_at IS NULL OR NEW.review_bypassed_by_id IS NULL
           OR NEW.review_bypass_reason <> 'formatting_only_descriptive_patch'
           OR EXISTS (SELECT 1 FROM datasets_datasetproposalvote v WHERE v.proposal_id = OLD.id) THEN
            RAISE EXCEPTION 'automatic approval is restricted to audited formatting-only PATCH proposals' USING ERRCODE = '23514';
        END IF;
        IF (to_jsonb(NEW) - ARRAY['status', 'submitted_at', 'decided_at', 'computed_min_change_type', 'review_bypassed_at', 'review_bypassed_by_id', 'review_bypass_reason', 'last_activity_at'])
           <> (to_jsonb(OLD) - ARRAY['status', 'submitted_at', 'decided_at', 'computed_min_change_type', 'review_bypassed_at', 'review_bypassed_by_id', 'review_bypass_reason', 'last_activity_at']) THEN
            RAISE EXCEPTION 'proposal content cannot change during automatic review bypass' USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END IF;

    IF OLD.status = 'draft' AND NEW.status IN ('cancelled', 'stale') THEN
        IF (to_jsonb(NEW) - ARRAY['status', 'decided_at', 'last_activity_at'])
           <> (to_jsonb(OLD) - ARRAY['status', 'decided_at', 'last_activity_at']) THEN
            RAISE EXCEPTION 'proposal content cannot change while closing a draft' USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END IF;

    IF OLD.status = 'pending_review'
       AND NEW.status IN ('approved', 'rejected', 'changes_requested', 'stale', 'cancelled') THEN
        IF NEW.status IN ('approved', 'rejected') THEN
            SELECT COUNT(DISTINCT p.id) INTO reviewer_count
              FROM accounts_userprofile p JOIN accounts_userrole r ON r.profile_id = p.id AND r.role = 'reviewer';
            vote_quorum := CASE WHEN reviewer_count = 0 THEN 1 ELSE LEAST(3, reviewer_count) END;
            SELECT COUNT(*) FILTER (WHERE v.vote = 'approve'), COUNT(*) FILTER (WHERE v.vote = 'reject')
              INTO approve_count, reject_count
              FROM datasets_datasetproposalvote v
              JOIN accounts_userprofile p ON p.user_id = v.reviewer_id
              JOIN accounts_userrole r ON r.profile_id = p.id AND r.role = 'reviewer'
             WHERE v.proposal_id = OLD.id;
            IF approve_count + reject_count < vote_quorum
               OR (NEW.status = 'approved' AND approve_count <= reject_count)
               OR (NEW.status = 'rejected' AND reject_count <= approve_count) THEN
                RAISE EXCEPTION 'proposal decision does not satisfy reviewer quorum and majority' USING ERRCODE = '23514';
            END IF;
        ELSIF NEW.status = 'changes_requested' THEN
            IF NEW.decided_at IS NULL OR NOT EXISTS (
                SELECT 1 FROM datasets_datasetproposalactivity a
                 WHERE a.proposal_id = OLD.id AND a.action = 'changes_requested'
                   AND a.from_status = 'pending_review' AND a.to_status = 'changes_requested'
                   AND btrim(a.reason) <> ''
            ) THEN
                RAISE EXCEPTION 'requesting changes requires an audited reviewer explanation' USING ERRCODE = '23514';
            END IF;
        END IF;
        IF (to_jsonb(NEW) - ARRAY['status', 'decided_at', 'last_activity_at'])
           <> (to_jsonb(OLD) - ARRAY['status', 'decided_at', 'last_activity_at']) THEN
            RAISE EXCEPTION 'submitted proposal content cannot change during review decisions' USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END IF;

    IF OLD.status = 'changes_requested' AND NEW.status = 'draft' THEN
        IF NEW.submitted_at IS NOT NULL OR NEW.decided_at IS NOT NULL
           OR NEW.computed_min_change_type IS NOT NULL OR NEW.final_change_type IS NOT NULL
           OR NEW.review_bypassed_at IS NOT NULL OR NEW.review_bypassed_by_id IS NOT NULL
           OR NEW.review_bypass_reason <> '' OR NEW.resulting_release_id IS NOT NULL THEN
            RAISE EXCEPTION 'resubmission must reopen a clean draft without prior decision fields' USING ERRCODE = '23514';
        END IF;
        IF (to_jsonb(NEW) - ARRAY['status', 'submitted_at', 'decided_at', 'computed_min_change_type', 'final_change_type', 'review_bypassed_at', 'review_bypassed_by_id', 'review_bypass_reason', 'last_activity_at'])
           <> (to_jsonb(OLD) - ARRAY['status', 'submitted_at', 'decided_at', 'computed_min_change_type', 'final_change_type', 'review_bypassed_at', 'review_bypassed_by_id', 'review_bypass_reason', 'last_activity_at']) THEN
            RAISE EXCEPTION 'proposal content cannot change until the changes-requested draft is reopened' USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END IF;
    IF OLD.status = 'changes_requested' AND NEW.status IN ('stale', 'cancelled') THEN
        IF (to_jsonb(NEW) - ARRAY['status', 'decided_at', 'last_activity_at'])
           <> (to_jsonb(OLD) - ARRAY['status', 'decided_at', 'last_activity_at']) THEN
            RAISE EXCEPTION 'proposal content cannot change while closing a changes-requested proposal' USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END IF;

    IF OLD.status = 'approved' AND NEW.status IN ('published', 'stale', 'cancelled') THEN
        IF NEW.status = 'published' AND (
            NEW.resulting_release_id IS NULL OR NEW.published_at IS NULL
            OR NEW.final_change_type IS NULL OR NEW.final_change_type NOT IN ('patch', 'minor', 'major')
            OR NOT EXISTS (SELECT 1 FROM datasets_datasetrelease r WHERE r.id = NEW.resulting_release_id
                           AND r.dataset_id = NEW.dataset_id AND r.status = 'published')
        ) THEN
            RAISE EXCEPTION 'a proposal can be published only with its published resulting release' USING ERRCODE = '23514';
        END IF;
        IF NEW.status = 'published' THEN
            IF (to_jsonb(NEW) - ARRAY['status', 'final_change_type', 'resulting_release_id', 'published_at', 'last_activity_at'])
               <> (to_jsonb(OLD) - ARRAY['status', 'final_change_type', 'resulting_release_id', 'published_at', 'last_activity_at']) THEN
                RAISE EXCEPTION 'proposal content cannot change during publication' USING ERRCODE = '23514';
            END IF;
        ELSE
            IF (to_jsonb(NEW) - ARRAY['status', 'decided_at', 'last_activity_at'])
               <> (to_jsonb(OLD) - ARRAY['status', 'decided_at', 'last_activity_at']) THEN
                RAISE EXCEPTION 'proposal content cannot change while closing an approved proposal' USING ERRCODE = '23514';
            END IF;
        END IF;
        RETURN NEW;
    END IF;
    RAISE EXCEPTION 'invalid dataset proposal state transition' USING ERRCODE = '23514';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS ordp_proposal_content_immutable ON datasets_datasetproposal;
DROP FUNCTION IF EXISTS ordp_prevent_submitted_proposal_content_update();

CREATE OR REPLACE FUNCTION ordp_proposal_activity_immutable() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'proposal activity is append-only' USING ERRCODE = '23514';
END;
$$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS ordp_proposal_activity_immutable_trigger ON datasets_datasetproposalactivity;
CREATE TRIGGER ordp_proposal_activity_immutable_trigger
BEFORE UPDATE OR DELETE ON datasets_datasetproposalactivity
FOR EACH ROW EXECUTE FUNCTION ordp_proposal_activity_immutable();
"""

REVERSE_SQL = r"""
DROP TRIGGER IF EXISTS ordp_proposal_activity_immutable_trigger ON datasets_datasetproposalactivity;
DROP FUNCTION IF EXISTS ordp_proposal_activity_immutable();
"""

class Migration(migrations.Migration):
    dependencies = [
        ("datasets", "0040_datasetproposal_review_bypass_reason_and_more"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]
    operations = [
        migrations.AlterField(
            model_name="datasetproposal", name="status",
            field=models.CharField(max_length=24, choices=[("draft", "Draft"), ("pending_review", "Pending review"), ("approved", "Approved"), ("rejected", "Rejected"), ("changes_requested", "Changes requested"), ("published", "Published"), ("stale", "Stale"), ("cancelled", "Cancelled")], default="draft"),
        ),
        migrations.RemoveConstraint(model_name="datasetproposal", name="one_active_proposal_per_dataset"),
        migrations.AddConstraint(
            model_name="datasetproposal",
            constraint=models.UniqueConstraint(fields=("dataset",), condition=models.Q(status__in=("draft", "pending_review", "changes_requested", "approved")), name="one_active_proposal_per_dataset"),
        ),
        migrations.CreateModel(
            name="DatasetProposalActivity",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("action", models.CharField(max_length=32, choices=[("content_updated", "Content updated"), ("submitted", "Submitted"), ("reopened_for_revision", "Reopened for revision"), ("resubmitted", "Resubmitted"), ("vote_cast", "Vote cast"), ("changes_requested", "Changes requested"), ("approved", "Approved"), ("rejected", "Rejected"), ("review_bypassed", "Review bypassed"), ("published", "Published"), ("cancelled", "Cancelled"), ("stale", "Marked stale")])),
                ("from_status", models.CharField(blank=True, default="", max_length=24)),
                ("to_status", models.CharField(blank=True, default="", max_length=24)),
                ("reason", models.TextField(blank=True, default="")),
                ("details", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("actor", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="dataset_proposal_activity", to=settings.AUTH_USER_MODEL)),
                ("proposal", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="activity", to="datasets.datasetproposal")),
            ],
            options={"ordering": ["created_at", "id"]},
        ),
        migrations.AddIndex(model_name="datasetproposalactivity", index=models.Index(fields=["proposal", "created_at"], name="ds_propact_prop_created_idx")),
        migrations.RunSQL(LIFECYCLE_SQL, reverse_sql=REVERSE_SQL),
    ]
