import uuid
from threading import Event, Thread
from time import sleep

from django.db import IntegrityError, connection, transaction
from django.db import close_old_connections
from django.db.models import ProtectedError
from django.test import TestCase, TransactionTestCase, skipUnlessDBFeature
from django.utils import timezone

from apps.datasets.factories import make_user
from apps.datasets.models import (
    Dataset,
    DatasetRelease,
    DatasetReleaseFile,
    DatasetProposal,
    DatasetProposalFile,
    DatasetProposalVote,
    StoredObject,
    ChangeType,
)
from apps.datasets.services.proposals import submit_proposal, submit_proposal_vote
def make_dataset(owner, title="Release DS"):
    return Dataset.objects.create(title=title, owner=owner, status=Dataset.Status.PUBLISHED)


def make_release(
    dataset, major=1, minor=0, patch=0, base=None,
    status=DatasetRelease.Status.PUBLISHED,
):
    release = DatasetRelease.objects.create(
        dataset=dataset, major=major, minor=minor, patch=patch,
        base_release=base, status=DatasetRelease.Status.DRAFT,
        metadata_snapshot={"schema_version": 1, "title": dataset.title},
    )
    if status == DatasetRelease.Status.PUBLISHED:
        stored_object = make_stored_object(dataset)
        DatasetReleaseFile.objects.create(
            release=release,
            stored_object=stored_object,
            logical_file_id=uuid.uuid4(),
        )
        release.status = DatasetRelease.Status.PUBLISHED
        release.save(update_fields=["status"])
        dataset.published_release = release
        dataset.save(update_fields=["published_release"])
    return release


def make_stored_object(dataset, filename="survey.csv", user=None):
    object_id = uuid.uuid4()
    return StoredObject.objects.create(
        id=object_id,
        dataset=dataset,
        storage_key=StoredObject.build_storage_key(dataset.id, object_id, filename),
        original_filename=filename,
        file_type="csv",
        file_size=10,
        checksum_sha256="a" * 64,
        uploaded_by=user,
    )


def submit_and_approve(proposal, owner):
    submit_proposal(proposal, owner, ChangeType.PATCH)
    reviewers = []
    for index in range(3):
        reviewer_id = uuid.uuid4().hex[:12]
        reviewers.append(make_user(
            f"release-reviewer-{reviewer_id}",
            f"release-reviewer-{reviewer_id}@aastu.edu.et",
            role="reviewer",
        ))
    for reviewer in reviewers:
        submit_proposal_vote(proposal, reviewer, DatasetProposalVote.Vote.APPROVE)
class ReleaseVersionTests(TestCase):
    def setUp(self):
        self.owner = make_user("relowner", "relowner@aastu.edu.et")
        self.dataset = make_dataset(self.owner)

    def test_version_property_formats_semver(self):
        release = make_release(self.dataset, 1, 10, 2)
        self.assertEqual(release.version, "1.10.2")

    def test_duplicate_version_on_same_dataset_is_rejected(self):
        make_release(self.dataset, 1, 0, 0)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                make_release(self.dataset, 1, 0, 0)

    def test_same_version_allowed_on_different_datasets(self):
        other = make_dataset(self.owner, "Other DS")
        make_release(self.dataset, 1, 0, 0)
        make_release(other, 1, 0, 0)  # must not raise
        self.assertEqual(DatasetRelease.objects.filter(major=1, minor=0, patch=0).count(), 2)

    def test_withdrawn_release_keeps_its_version_number_taken(self):
        release = make_release(self.dataset, 1, 0, 0)
        release.status = DatasetRelease.Status.WITHDRAWN
        release.withdrawn_at = timezone.now()
        release.withdrawal_reason = "Testing historical version reservation"
        release.save(update_fields=["status", "withdrawn_at", "withdrawal_reason"])
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                make_release(self.dataset, 1, 0, 0)

    def test_published_release_pointer_defaults_to_none_and_can_be_set(self):
        release = make_release(self.dataset)
        self.dataset.published_release = None
        self.dataset.save(update_fields=["published_release"])
        self.dataset.refresh_from_db()
        self.assertIsNone(self.dataset.published_release)
        self.dataset.published_release = release
        self.dataset.save(update_fields=["published_release"])
        self.dataset.refresh_from_db()
        self.assertEqual(self.dataset.published_release_id, release.id)
class StoredObjectTests(TestCase):
    def setUp(self):
        self.owner = make_user("soowner", "soowner@aastu.edu.et")
        self.dataset = make_dataset(self.owner)

    def test_same_filename_gets_different_storage_keys(self):
        first = make_stored_object(self.dataset, "survey.csv")
        second = make_stored_object(self.dataset, "survey.csv")
        self.assertNotEqual(first.storage_key, second.storage_key)

    def test_storage_key_format(self):
        obj = make_stored_object(self.dataset, "survey.csv")
        self.assertEqual(
            obj.storage_key,
            f"objects/{self.dataset.id}/{obj.id}/survey.csv",
        )

    def test_duplicate_storage_key_is_rejected(self):
        first = make_stored_object(self.dataset)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                StoredObject.objects.create(
                    dataset=self.dataset, storage_key=first.storage_key,
                    original_filename="x.csv", file_type="csv",
                    file_size=1, checksum_sha256="b" * 64,
                )
class ReleaseManifestTests(TestCase):
    def setUp(self):
        self.owner = make_user("rmowner", "rmowner@aastu.edu.et")
        self.dataset = make_dataset(self.owner)
        self.release = make_release(self.dataset, status=DatasetRelease.Status.DRAFT)
        self.obj = make_stored_object(self.dataset)

    def test_replacement_keeps_logical_id_across_releases(self):
        logical_id = uuid.uuid4()
        DatasetReleaseFile.objects.create(
            release=self.release, stored_object=self.obj, logical_file_id=logical_id,
        )
        newer = make_release(
            self.dataset, 1, 0, 1, base=self.release, status=DatasetRelease.Status.DRAFT,
        )
        replacement = make_stored_object(self.dataset)  # same filename, new object
        DatasetReleaseFile.objects.create(
            release=newer, stored_object=replacement, logical_file_id=logical_id,
        )
        # Old release still points at the original physical object.
        self.assertEqual(self.release.files.get().stored_object_id, self.obj.id)
        self.assertEqual(newer.files.get().stored_object_id, replacement.id)

    def test_logical_file_cannot_appear_twice_in_one_release(self):
        logical_id = uuid.uuid4()
        DatasetReleaseFile.objects.create(
            release=self.release, stored_object=self.obj, logical_file_id=logical_id,
        )
        other_obj = make_stored_object(self.dataset, "b.csv")
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                DatasetReleaseFile.objects.create(
                    release=self.release, stored_object=other_obj, logical_file_id=logical_id,
                )

    def test_same_object_cannot_appear_twice_in_one_release(self):
        DatasetReleaseFile.objects.create(
            release=self.release, stored_object=self.obj, logical_file_id=uuid.uuid4(),
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                DatasetReleaseFile.objects.create(
                    release=self.release, stored_object=self.obj, logical_file_id=uuid.uuid4(),
                )


class PostgreSQLReleaseIntegrityTriggerTests(TestCase):
    def setUp(self):
        self.owner = make_user("release-trigger-owner", "release-trigger-owner@aastu.edu.et")
        self.dataset = make_dataset(self.owner)
        self.published_release = make_release(self.dataset)
        self.draft_release = make_release(
            self.dataset, 1, 0, 1, base=self.published_release,
            status=DatasetRelease.Status.DRAFT,
        )
        self.stored_object = self.published_release.files.first().stored_object
        self.entry = self.published_release.files.first()

    def _require_postgresql(self):
        if connection.vendor != "postgresql":
            self.skipTest("Release integrity triggers are PostgreSQL-specific.")

    @skipUnlessDBFeature("supports_transactions")
    def test_published_manifest_cannot_be_moved_to_a_draft_release(self):
        self._require_postgresql()
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                DatasetReleaseFile.objects.filter(pk=self.entry.pk).update(
                    release=self.draft_release
                )

    @skipUnlessDBFeature("supports_transactions")
    def test_raw_sql_cannot_change_published_logical_file_identity(self):
        self._require_postgresql()
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        UPDATE datasets_datasetreleasefile
                           SET logical_file_id = %s
                         WHERE id = %s
                        """,
                        [uuid.uuid4(), self.entry.pk],
                    )

    @skipUnlessDBFeature("supports_transactions")
    def test_direct_published_release_insert_with_empty_manifest_is_rejected(self):
        self._require_postgresql()
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        INSERT INTO datasets_datasetrelease (
                            id, dataset_id, major, minor, patch, status,
                            change_summary, release_notes, snapshot_schema_version,
                            metadata_snapshot, license_snapshot, citation_snapshot,
                            published_at, withdrawal_reason
                        ) VALUES (
                            %s, %s, 9, 0, 0, 'published',
                            '', '', 1, %s::jsonb, %s::jsonb, %s::jsonb,
                            CURRENT_TIMESTAMP, ''
                        )
                        """,
                        [
                            uuid.uuid4(),
                            self.dataset.pk,
                            '{"schema_version": 1}',
                            "{}",
                            "{}",
                        ],
                    )

    @skipUnlessDBFeature("supports_transactions")
    def test_dataset_pointer_cannot_reference_a_draft_release(self):
        self._require_postgresql()
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        UPDATE datasets_dataset
                           SET published_release_id = %s
                         WHERE id = %s
                        """,
                        [self.draft_release.pk, self.dataset.pk],
                    )

    def test_application_cannot_create_published_release_directly(self):
        with self.assertRaises(RuntimeError):
            DatasetRelease.objects.create(
                dataset=self.dataset,
                major=9,
                minor=0,
                patch=0,
                status=DatasetRelease.Status.PUBLISHED,
                metadata_snapshot={"schema_version": 1},
            )


class ProtectionTests(TestCase):
    def setUp(self):
        self.owner = make_user("prowner", "prowner@aastu.edu.et")
        self.dataset = make_dataset(self.owner)
        self.release = make_release(self.dataset, status=DatasetRelease.Status.DRAFT)
        self.obj = make_stored_object(self.dataset)
        DatasetReleaseFile.objects.create(
            release=self.release, stored_object=self.obj, logical_file_id=uuid.uuid4(),
        )

    def test_cannot_delete_stored_object_referenced_by_a_release(self):
        with self.assertRaises(ProtectedError):
            self.obj.delete()

    def test_cannot_delete_release_that_has_manifest_entries(self):
        with self.assertRaises(ProtectedError):
            self.release.delete()

    def test_cannot_delete_dataset_that_has_releases(self):
        with self.assertRaises(ProtectedError):
            self.dataset.delete()

    def test_cannot_delete_release_that_is_a_base_of_another(self):
        parent = make_release(
            self.dataset, 1, 0, 1, status=DatasetRelease.Status.DRAFT
        )
        child = make_release(
            self.dataset, 1, 0, 2, base=parent,
            status=DatasetRelease.Status.DRAFT,
        )
        self.release = parent
        DatasetReleaseFile.objects.filter(release=parent).delete()
        with self.assertRaises(ProtectedError):
            self.release.delete()
        self.assertTrue(DatasetRelease.objects.filter(id=child.id).exists())

    @skipUnlessDBFeature("supports_transactions")
    def test_postgresql_triggers_reject_cross_dataset_manifest_links(self):
        if connection.vendor != "postgresql":
            self.skipTest("Cross-table integrity triggers are PostgreSQL-specific.")

        other_dataset = make_dataset(self.owner, "Other manifest dataset")
        foreign_object = make_stored_object(other_dataset, "foreign.csv")
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                DatasetReleaseFile.objects.create(
                    release=self.release,
                    stored_object=foreign_object,
                    logical_file_id=uuid.uuid4(),
                )

    @skipUnlessDBFeature("supports_transactions")
    def test_postgresql_triggers_reject_queryset_mutation_of_stored_objects(self):
        if connection.vendor != "postgresql":
            self.skipTest("Immutability triggers are PostgreSQL-specific.")

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                StoredObject.objects.filter(pk=self.obj.pk).update(storage_key="changed")
class ProposalVoteTests(TestCase):
    def setUp(self):
        self.owner = make_user("pvowner", "pvowner@aastu.edu.et")
        self.reviewer = make_user("pvreviewer", "pvreviewer@aastu.edu.et", role="reviewer")
        self.dataset = make_dataset(self.owner)
        self.release = make_release(self.dataset)
        self.proposal = DatasetProposal.objects.create(
            dataset=self.dataset, base_release=self.release,
            source=DatasetProposal.Source.OWNER_EDIT, submitted_by=self.owner,
        )

    def test_reviewer_cannot_vote_twice(self):
        DatasetProposalVote.objects.create(
            proposal=self.proposal, reviewer=self.reviewer, vote="approve",
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                DatasetProposalVote.objects.create(
                    proposal=self.proposal, reviewer=self.reviewer, vote="reject", reason="changed mind",
                )

    def test_reject_without_reason_is_rejected_by_database(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                DatasetProposalVote.objects.create(
                    proposal=self.proposal, reviewer=self.reviewer, vote="reject", reason="",
                )

    def test_approve_without_reason_is_allowed(self):
        DatasetProposalVote.objects.create(
            proposal=self.proposal, reviewer=self.reviewer, vote="approve",
        )
        self.assertEqual(self.proposal.votes.count(), 1)

    def test_proposal_defaults_to_draft(self):
        self.assertEqual(self.proposal.status, DatasetProposal.Status.DRAFT)

    def test_one_release_cannot_come_from_two_proposals(self):
        submit_and_approve(self.proposal, self.owner)
        newer = make_release(self.dataset, 1, 0, 1, base=self.release)
        DatasetProposal.objects.transition(
            self.proposal.pk,
            DatasetProposal.Status.PUBLISHED,
            resulting_release_id=newer.pk,
            final_change_type=ChangeType.PATCH,
            published_at=timezone.now(),
        )
        second = DatasetProposal.objects.create(
            dataset=self.dataset, base_release=self.release,
            source=DatasetProposal.Source.OWNER_EDIT, submitted_by=self.owner,
        )
        second.resulting_release = newer
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                second.save(update_fields=["resulting_release"])



class PostgreSQLProposalLifecycleProtectionTests(TestCase):
    def setUp(self):
        self.owner = make_user("plifecycleowner", "plifecycleowner@aastu.edu.et")
        self.dataset = make_dataset(self.owner, "Proposal lifecycle guard")
        self.release = make_release(self.dataset)
        self.proposal = DatasetProposal.objects.create(
            dataset=self.dataset,
            base_release=self.release,
            source=DatasetProposal.Source.OWNER_EDIT,
            submitted_by=self.owner,
            proposed_metadata={"title": "Initial title"},
        )

    def _require_postgresql(self):
        if connection.vendor != "postgresql":
            self.skipTest("Proposal lifecycle database triggers require PostgreSQL.")

    def test_draft_edits_and_submission_are_allowed(self):
        self._require_postgresql()
        # Draft content edits must remain possible before submission.
        self.proposal.proposed_metadata = {"title": "Edited while draft"}
        self.proposal.save(update_fields=["proposed_metadata"])

        submitted = submit_proposal(self.proposal, self.owner, ChangeType.PATCH)
        self.assertEqual(submitted.status, DatasetProposal.Status.PENDING_REVIEW)

    def test_review_activity_timestamp_can_change_without_changing_content(self):
        self._require_postgresql()
        submitted = submit_proposal(self.proposal, self.owner, ChangeType.PATCH)
        submitted.last_activity_at = timezone.now()
        submitted.save(update_fields=["last_activity_at"])
        submitted.refresh_from_db()
        self.assertIsNotNone(submitted.last_activity_at)

    def test_submitted_lifecycle_fields_cannot_be_changed_without_transition(self):
        self._require_postgresql()
        submitted = submit_proposal(self.proposal, self.owner, ChangeType.PATCH)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute(
                        "UPDATE datasets_datasetproposal "
                        "SET final_change_type = %s WHERE id = %s",
                        [ChangeType.MAJOR, submitted.pk],
                    )

class PostgreSQLProposalFileTriggerTests(TestCase):
    def setUp(self):
        self.owner = make_user("pfiletriggerowner", "pfiletriggerowner@aastu.edu.et")
        self.dataset = make_dataset(self.owner)
        self.release = make_release(self.dataset)
        self.proposal = DatasetProposal.objects.create(
            dataset=self.dataset,
            base_release=self.release,
            source=DatasetProposal.Source.OWNER_EDIT,
            submitted_by=self.owner,
            computed_min_change_type=ChangeType.PATCH,
        )
        self.stored_object = make_stored_object(self.dataset)
        self.entry = DatasetProposalFile.objects.create(
            proposal=self.proposal,
            stored_object=self.stored_object,
            logical_file_id=uuid.uuid4(),
        )
        submit_proposal(self.proposal, self.owner, ChangeType.PATCH)

    def _require_postgresql(self):
        if connection.vendor != "postgresql":
            self.skipTest("Proposal-file database triggers are PostgreSQL-specific.")

    @skipUnlessDBFeature("supports_transactions")
    def test_postgresql_trigger_rejects_insert_after_approval(self):
        self._require_postgresql()
        another_object = make_stored_object(self.dataset, "insert-after-approval.csv")
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        INSERT INTO datasets_datasetproposalfile
                            (id, proposal_id, stored_object_id, logical_file_id,
                             display_order)
                        VALUES (%s, %s, %s, %s, %s)
                        """,
                        [
                            uuid.uuid4(),
                            self.proposal.id,
                            another_object.id,
                            uuid.uuid4(),
                            1,
                        ],
                    )

    @skipUnlessDBFeature("supports_transactions")
    def test_postgresql_trigger_rejects_update_after_approval(self):
        self._require_postgresql()
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute(
                        "UPDATE datasets_datasetproposalfile SET display_order = 9 WHERE id = %s",
                        [self.entry.id],
                    )

    @skipUnlessDBFeature("supports_transactions")
    def test_postgresql_trigger_rejects_delete_after_approval(self):
        self._require_postgresql()
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute(
                        "DELETE FROM datasets_datasetproposalfile WHERE id = %s",
                        [self.entry.id],
                    )

    @skipUnlessDBFeature("supports_transactions")
    def test_postgresql_trigger_rejects_moving_file_between_proposals(self):
        self._require_postgresql()
        other_dataset = make_dataset(self.owner, "Other moving dataset")
        other_release = make_release(other_dataset)
        other_proposal = DatasetProposal.objects.create(
            dataset=other_dataset,
            base_release=other_release,
            source=DatasetProposal.Source.OWNER_EDIT,
        )
        DatasetProposal.objects.transition(
            other_proposal.pk, DatasetProposal.Status.CANCELLED
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute(
                        "UPDATE datasets_datasetproposalfile SET proposal_id = %s WHERE id = %s",
                        [other_proposal.id, self.entry.id],
                    )

    @skipUnlessDBFeature("supports_transactions")
    def test_postgresql_trigger_rejects_cross_dataset_stored_object_reference(self):
        self._require_postgresql()
        other_dataset = make_dataset(self.owner, "Other trigger dataset")
        other_release = make_release(other_dataset)
        draft_proposal = DatasetProposal.objects.create(
            dataset=other_dataset,
            base_release=other_release,
            source=DatasetProposal.Source.OWNER_EDIT,
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        INSERT INTO datasets_datasetproposalfile
                            (id, proposal_id, stored_object_id, logical_file_id,
                             display_order)
                        VALUES (%s, %s, %s, %s, %s)
                        """,
                        [
                            uuid.uuid4(),
                            draft_proposal.id,
                            self.stored_object.id,
                            uuid.uuid4(),
                            0,
                        ],
                    )

    @skipUnlessDBFeature("supports_transactions")
    def test_postgresql_trigger_rejects_submitted_proposal_content_update(self):
        self._require_postgresql()
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        UPDATE datasets_datasetproposal
                        SET proposed_metadata = '{"title": "Tampered"}'::jsonb
                        WHERE id = %s
                        """,
                        [self.proposal.id],
                    )

    @skipUnlessDBFeature("supports_transactions")
    def test_postgresql_trigger_rejects_submitted_proposal_reset_to_draft(self):
        self._require_postgresql()
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        UPDATE datasets_datasetproposal SET status = 'draft'
                        WHERE id = %s
                        """,
                        [self.proposal.id],
                    )

    @skipUnlessDBFeature("supports_transactions")
    def test_postgresql_trigger_rejects_approval_without_reviewer_quorum(self):
        self._require_postgresql()
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute(
                        """
                        UPDATE datasets_datasetproposal
                           SET status = 'approved'
                         WHERE id = %s
                        """,
                        [self.proposal.id],
                    )

    @skipUnlessDBFeature("supports_transactions")
    def test_postgresql_allows_cascading_delete_of_draft_proposal(self):
        self._require_postgresql()
        draft_dataset = make_dataset(self.owner, "Draft cascade dataset")
        draft_release = make_release(draft_dataset)
        draft_object = make_stored_object(draft_dataset)
        draft_proposal = DatasetProposal.objects.create(
            dataset=draft_dataset,
            base_release=draft_release,
            source=DatasetProposal.Source.OWNER_EDIT,
        )
        DatasetProposalFile.objects.create(
            proposal=draft_proposal,
            stored_object=draft_object,
            logical_file_id=uuid.uuid4(),
        )
        draft_proposal.delete()
        self.assertFalse(DatasetProposal.objects.filter(pk=draft_proposal.pk).exists())


class PostgreSQLProposalTransitionRaceTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.owner = make_user("proposalraceowner", "proposalraceowner@aastu.edu.et")
        self.dataset = make_dataset(self.owner)
        self.release = make_release(self.dataset)
        self.proposal = DatasetProposal.objects.create(
            dataset=self.dataset,
            base_release=self.release,
            source=DatasetProposal.Source.OWNER_EDIT,
            submitted_by=self.owner,
            computed_min_change_type=ChangeType.PATCH,
        )
        self.stored_object = make_stored_object(self.dataset)

    @skipUnlessDBFeature("supports_transactions")
    def test_parent_row_lock_serializes_submission_and_manifest_insert(self):
        if connection.vendor != "postgresql":
            self.skipTest("Proposal transition concurrency requires PostgreSQL.")

        transition_locked = Event()
        finish_transition = Event()
        mutation_started = Event()
        transition_errors = []
        mutation_results = []

        def submit_proposal():
            close_old_connections()
            try:
                with transaction.atomic():
                    Dataset.objects.select_for_update().get(pk=self.dataset.pk)
                    proposal = DatasetProposal.objects.select_for_update().get(
                        pk=self.proposal.pk
                    )
                    transition_locked.set()
                    if not finish_transition.wait(timeout=10):
                        raise TimeoutError("Timed out waiting to finish proposal transition.")
                    submitted_at = timezone.now()
                    DatasetProposal.objects.transition(
                        proposal.pk,
                        DatasetProposal.Status.PENDING_REVIEW,
                        submitted_at=submitted_at,
                        last_activity_at=submitted_at,
                        computed_min_change_type=ChangeType.PATCH,
                    )
            except Exception as exc:
                transition_errors.append(exc)
            finally:
                close_old_connections()

        def insert_file():
            close_old_connections()
            try:
                mutation_started.set()
                with transaction.atomic():
                    with connection.cursor() as cursor:
                        cursor.execute(
                            """
                            INSERT INTO datasets_datasetproposalfile
                                (id, proposal_id, stored_object_id, logical_file_id,
                                         display_order)
                                    VALUES (%s, %s, %s, %s, %s)
                            """,
                            [
                                uuid.uuid4(),
                                self.proposal.pk,
                                self.stored_object.pk,
                                uuid.uuid4(),
                                0,
                            ],
                        )
                mutation_results.append("inserted")
            except IntegrityError:
                mutation_results.append("rejected")
            except Exception as exc:
                mutation_results.append(exc)
            finally:
                close_old_connections()

        transition_thread = Thread(target=submit_proposal)
        transition_thread.start()
        self.assertTrue(transition_locked.wait(timeout=10))
        mutation_thread = Thread(target=insert_file)
        mutation_thread.start()
        self.assertTrue(mutation_started.wait(timeout=10))
        sleep(0.1)
        finish_transition.set()
        transition_thread.join(timeout=10)
        mutation_thread.join(timeout=10)

        self.assertFalse(transition_thread.is_alive())
        self.assertFalse(mutation_thread.is_alive())
        self.assertEqual(transition_errors, [])
        self.assertEqual(mutation_results, ["rejected"])
        self.proposal.refresh_from_db()
        self.assertEqual(self.proposal.status, DatasetProposal.Status.PENDING_REVIEW)
        self.assertFalse(DatasetProposalFile.objects.filter(proposal=self.proposal).exists())


class OneActiveProposalTests(TestCase):
    def setUp(self):
        self.owner = make_user("oapowner", "oapowner@aastu.edu.et")
        self.dataset = make_dataset(self.owner)
        self.release = make_release(self.dataset)

    def _new_proposal(self, dataset=None, status=DatasetProposal.Status.DRAFT):
        dataset = dataset or self.dataset
        base_release = dataset.published_release or make_release(dataset)

        proposal = DatasetProposal.objects.create(
            dataset=dataset,
            base_release=base_release,
            source=DatasetProposal.Source.OWNER_EDIT,
            submitted_by=self.owner,
            computed_min_change_type=ChangeType.PATCH,
        )

        if status == DatasetProposal.Status.DRAFT:
            return proposal

        if status == DatasetProposal.Status.PENDING_REVIEW:
            submit_proposal(proposal, self.owner, ChangeType.PATCH)

        elif status == DatasetProposal.Status.APPROVED:
            submit_and_approve(proposal, self.owner)

        elif status == DatasetProposal.Status.REJECTED:
            submit_proposal(proposal, self.owner, ChangeType.PATCH)

            reviewers = []
            for index in range(3):
                reviewer_id = uuid.uuid4().hex[:12]
                reviewers.append(make_user(
                    f"reject-reviewer-{reviewer_id}",
                    f"reject-reviewer-{reviewer_id}@aastu.edu.et",
                    role="reviewer",
                ))

            for reviewer in reviewers:
                submit_proposal_vote(
                    proposal,
                    reviewer,
                    DatasetProposalVote.Vote.REJECT,
                    reason="Testing reviewer rejection",
                )

        elif status == DatasetProposal.Status.CANCELLED:
            DatasetProposal.objects.transition(
                proposal.pk,
                DatasetProposal.Status.CANCELLED,
            )

        elif status == DatasetProposal.Status.STALE:
            DatasetProposal.objects.transition(
                proposal.pk,
                DatasetProposal.Status.STALE,
            )

        elif status == DatasetProposal.Status.PUBLISHED:
            submit_and_approve(proposal, self.owner)

            result = make_release(
                dataset,
                base_release.major,
                base_release.minor,
                base_release.patch + 1,
                base=base_release,
            )

            DatasetProposal.objects.transition(
                proposal.pk,
                DatasetProposal.Status.PUBLISHED,
                resulting_release_id=result.pk,
                final_change_type=ChangeType.PATCH,
                published_at=timezone.now(),
            )

        return DatasetProposal.objects.get(pk=proposal.pk)

    def test_second_active_proposal_is_rejected_for_each_active_status(self):
        for active in (
            DatasetProposal.Status.DRAFT,
            DatasetProposal.Status.PENDING_REVIEW,
            DatasetProposal.Status.APPROVED,
        ):
            proposal = self._new_proposal(status=active)

            with self.assertRaises(IntegrityError):
                with transaction.atomic():
                    self._new_proposal()

            if active == DatasetProposal.Status.DRAFT:
                DatasetProposal.objects.transition(
                    proposal.pk,
                    DatasetProposal.Status.CANCELLED,
                )

            elif active == DatasetProposal.Status.PENDING_REVIEW:
                reviewers = []

                for index in range(3):
                    reviewer_id = uuid.uuid4().hex[:12]
                    reviewers.append(make_user(
                        f"cancel-reviewer-{reviewer_id}",
                        f"cancel-reviewer-{reviewer_id}@aastu.edu.et",
                        role="reviewer",
                    ))

                for reviewer in reviewers:
                    submit_proposal_vote(
                        proposal,
                        reviewer,
                        DatasetProposalVote.Vote.REJECT,
                        reason="Testing reviewer rejection",
                    )

            else:
                DatasetProposal.objects.transition(
                    proposal.pk,
                    DatasetProposal.Status.CANCELLED,
                )

    def test_new_proposal_allowed_after_a_finished_status(self):
        for finished in (
            DatasetProposal.Status.PUBLISHED,
            DatasetProposal.Status.REJECTED,
            DatasetProposal.Status.CANCELLED,
            DatasetProposal.Status.STALE,
        ):
            DatasetProposal.objects.filter(
                dataset=self.dataset,
                status=DatasetProposal.Status.DRAFT,
            ).delete()

            self._new_proposal(status=finished)
            self._new_proposal()

    def test_many_finished_proposals_can_coexist(self):
        for finished in (
            DatasetProposal.Status.PUBLISHED,
            DatasetProposal.Status.REJECTED,
            DatasetProposal.Status.CANCELLED,
            DatasetProposal.Status.STALE,
        ):
            self._new_proposal(status=finished)

        self.assertEqual(
            DatasetProposal.objects.filter(dataset=self.dataset).count(),
            4,
        )

    def test_different_datasets_can_each_have_an_active_proposal(self):
        other = make_dataset(self.owner, "Other DS")

        self._new_proposal()

        DatasetProposal.objects.create(
            dataset=other,
            base_release=make_release(other),
            source=DatasetProposal.Source.OWNER_EDIT,
            submitted_by=self.owner,
            computed_min_change_type=ChangeType.PATCH,
        )

    def test_changing_status_to_active_while_another_is_active_is_rejected(self):
        finished = self._new_proposal(
            status=DatasetProposal.Status.REJECTED,
        )

        self._new_proposal()

        finished.status = DatasetProposal.Status.DRAFT

        with self.assertRaises(RuntimeError):
            finished.save(update_fields=["status"])

        finished.refresh_from_db()

        self.assertEqual(
            finished.status,
            DatasetProposal.Status.REJECTED,
        )