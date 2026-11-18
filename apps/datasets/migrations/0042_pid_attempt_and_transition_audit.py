from django.db import migrations, models
import django.utils.timezone
import uuid


CREATE_PID_AUDIT_TRIGGERS = r"""
CREATE OR REPLACE FUNCTION ordp_guard_doi_registration_attempt() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'DOI registration attempt history is immutable'
            USING ERRCODE = '23514';
    END IF;
    IF OLD.status <> 'pending'
       OR NEW.identifier_id IS DISTINCT FROM OLD.identifier_id
       OR NEW.attempt_number IS DISTINCT FROM OLD.attempt_number
       OR NEW.attempted_at IS DISTINCT FROM OLD.attempted_at
       OR NEW.request_summary IS DISTINCT FROM OLD.request_summary
       OR NEW.status NOT IN ('succeeded', 'failed') THEN
        RAISE EXCEPTION 'DOI registration attempts may transition once from pending to terminal'
            USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS ordp_doi_attempt_guard ON datasets_doiregistrationattempt;
CREATE TRIGGER ordp_doi_attempt_guard
BEFORE UPDATE OR DELETE ON datasets_doiregistrationattempt
FOR EACH ROW EXECUTE FUNCTION ordp_guard_doi_registration_attempt();

CREATE OR REPLACE FUNCTION ordp_guard_proposal_vote_update() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'proposal vote records cannot be updated; resubmission history is retained in proposal activity'
        USING ERRCODE = '23514';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS ordp_proposal_vote_update_guard ON datasets_datasetproposalvote;
CREATE TRIGGER ordp_proposal_vote_update_guard
BEFORE UPDATE ON datasets_datasetproposalvote
FOR EACH ROW EXECUTE FUNCTION ordp_guard_proposal_vote_update();

CREATE OR REPLACE FUNCTION ordp_audit_pid_outbox_transition() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        INSERT INTO datasets_pidregistrationevent
            (id, event_id, event_type, from_status, to_status, retry_count, detail, created_at)
        VALUES
            (gen_random_uuid(), NEW.id, NEW.event_type, '', NEW.status,
             NEW.retry_count, COALESCE(NEW.last_error, ''), CURRENT_TIMESTAMP);
    ELSIF NEW.status IS DISTINCT FROM OLD.status THEN
        INSERT INTO datasets_pidregistrationevent
            (id, event_id, event_type, from_status, to_status, retry_count, detail, created_at)
        VALUES
            (gen_random_uuid(), NEW.id, NEW.event_type, OLD.status, NEW.status,
             NEW.retry_count, COALESCE(NEW.last_error, ''), CURRENT_TIMESTAMP);
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS ordp_pid_outbox_transition_audit ON datasets_outboxevent;
CREATE TRIGGER ordp_pid_outbox_transition_audit
AFTER INSERT OR UPDATE OF status ON datasets_outboxevent
FOR EACH ROW EXECUTE FUNCTION ordp_audit_pid_outbox_transition();

CREATE OR REPLACE FUNCTION ordp_guard_pid_registration_event() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'PID registration audit events are immutable'
        USING ERRCODE = '23514';
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS ordp_pid_registration_event_guard ON datasets_pidregistrationevent;
CREATE TRIGGER ordp_pid_registration_event_guard
BEFORE UPDATE OR DELETE ON datasets_pidregistrationevent
FOR EACH ROW EXECUTE FUNCTION ordp_guard_pid_registration_event();
"""


DROP_PID_AUDIT_TRIGGERS = r"""
DROP TRIGGER IF EXISTS ordp_pid_registration_event_guard ON datasets_pidregistrationevent;
DROP FUNCTION IF EXISTS ordp_guard_pid_registration_event();
DROP TRIGGER IF EXISTS ordp_pid_outbox_transition_audit ON datasets_outboxevent;
DROP FUNCTION IF EXISTS ordp_audit_pid_outbox_transition();
DROP TRIGGER IF EXISTS ordp_proposal_vote_update_guard ON datasets_datasetproposalvote;
DROP FUNCTION IF EXISTS ordp_guard_proposal_vote_update();
DROP TRIGGER IF EXISTS ordp_doi_attempt_guard ON datasets_doiregistrationattempt;
DROP FUNCTION IF EXISTS ordp_guard_doi_registration_attempt();
"""


def install_guards(apps, schema_editor):
    if schema_editor.connection.vendor == "postgresql":
        with schema_editor.connection.cursor() as cursor:
            # Establish a baseline audit row for queue records that predate
            # this migration. Future status changes are captured by the trigger.
            cursor.execute("""
                INSERT INTO datasets_pidregistrationevent
                    (id, event_id, event_type, from_status, to_status, retry_count, detail, created_at)
                SELECT gen_random_uuid(), id, event_type, '', status, retry_count,
                       COALESCE(last_error, ''), created_at
                  FROM datasets_outboxevent
            """)
            cursor.execute(CREATE_PID_AUDIT_TRIGGERS)


def remove_guards(apps, schema_editor):
    if schema_editor.connection.vendor == "postgresql":
        with schema_editor.connection.cursor() as cursor:
            cursor.execute(DROP_PID_AUDIT_TRIGGERS)


class Migration(migrations.Migration):
    dependencies = [("datasets", "0041_proposal_feedback_audit")]

    operations = [
        migrations.CreateModel(
            name="PIDRegistrationEvent",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("event_id", models.UUIDField(db_index=True)),
                ("event_type", models.CharField(max_length=64)),
                ("from_status", models.CharField(blank=True, default="", max_length=16)),
                ("to_status", models.CharField(max_length=16)),
                ("retry_count", models.PositiveIntegerField(default=0)),
                ("detail", models.TextField(blank=True, default="")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={"ordering": ["created_at", "id"]},
        ),
        migrations.AddIndex(
            model_name="pidregistrationevent",
            index=models.Index(fields=["event_id", "created_at"], name="pid_event_audit_idx"),
        ),
        migrations.RunPython(install_guards, remove_guards),
    ]
