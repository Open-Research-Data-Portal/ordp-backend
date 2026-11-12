import uuid

from django.db import IntegrityError, transaction, connection, DatabaseError
from django.test import TestCase
from django.utils import timezone

from apps.datasets.factories import make_user
from apps.datasets.models import (
    ChangeType,
    Dataset,
    DatasetProposal,
    DatasetProposalFile,
    DatasetProposalVote,
    DatasetProposalActivity,
    DatasetRelease,
    DatasetReleaseFile,
    StoredObject,
)
from apps.datasets.services.proposals import (
    DuplicateProposalVoteError,
    ProposalLifecycleError,
    UnauthorizedProposalReviewerError,
    cancel_proposal,
    submit_proposal,
    submit_proposal_vote,
    update_draft_proposal,
)


class ProposalLifecycleTests(TestCase):
    def setUp(self):
        self.owner = make_user("lifecycle-owner", "lifecycle-owner@aastu.edu.et")
        self.dataset = Dataset.objects.create(
            title="Lifecycle",
            owner=self.owner,
            status=Dataset.Status.PUBLISHED,
        )
        self.stored_object = StoredObject.objects.create(
            dataset=self.dataset,
            storage_key=f"objects/{self.dataset.id}/{uuid.uuid4()}/data.csv",
            original_filename="data.csv",
            file_type="csv",
            file_size=5,
            checksum_sha256="a" * 64,
        )
        self.release = DatasetRelease.objects.create(
            dataset=self.dataset,
            major=1,
            minor=0,
            patch=0,
            metadata_snapshot={"schema_version": 1, "title": self.dataset.title},
        )
        DatasetReleaseFile.objects.create(
            release=self.release,
            stored_object=self.stored_object,
            logical_file_id=uuid.uuid4(),
        )
        self.release.status = DatasetRelease.Status.PUBLISHED
        self.release.save(update_fields=["status"])
        self.dataset.published_release = self.release
        self.dataset.save(update_fields=["published_release"])
        self.proposal = DatasetProposal.objects.create(
            dataset=self.dataset,
            base_release=self.release,
            source=DatasetProposal.Source.OWNER_EDIT,
            submitted_by=self.owner,
            computed_min_change_type=ChangeType.PATCH,
        )
        self.reviewers = [
            make_user(
                f"lifecycle-reviewer-{index}",
                f"lifecycle-reviewer-{index}@aastu.edu.et",
                role="reviewer",
            )
            for index in range(3)
        ]


    def test_reviewer_can_request_changes_with_required_feedback(self):
        submit_proposal(self.proposal, self.owner)
        reviewer = self.reviewers[0]
        with self.assertRaises(ValueError):
            from apps.datasets.services.proposals import request_proposal_changes
            request_proposal_changes(self.proposal, reviewer, "  ")
        updated = request_proposal_changes(
            self.proposal, reviewer, "Please correct the metadata and explain the file removal."
        )
        self.assertEqual(updated.status, DatasetProposal.Status.CHANGES_REQUESTED)
        event = updated.activity.filter(action="changes_requested").latest("created_at")
        self.assertEqual(event.actor_id, reviewer.pk)
        self.assertIn("correct the metadata", event.reason)

    def test_submitter_can_revise_and_resubmit_after_requested_changes(self):
        from apps.datasets.services.proposals import request_proposal_changes, resubmit_requested_proposal
        submit_proposal(self.proposal, self.owner)
        submit_proposal_vote(self.proposal, self.reviewers[0], DatasetProposalVote.Vote.APPROVE)
        request_proposal_changes(self.proposal, self.reviewers[1], "Please update the summary.")
        draft = resubmit_requested_proposal(self.proposal, self.owner)
        self.assertEqual(draft.status, DatasetProposal.Status.DRAFT)
        self.assertIsNone(draft.submitted_at)
        self.assertFalse(draft.votes.exists())
        self.assertTrue(draft.activity.filter(action=DatasetProposalActivity.Action.VOTE_CAST).exists())
        draft = update_draft_proposal(draft, self.owner, change_summary="Updated summary after review feedback")
        submitted = submit_proposal(draft, self.owner)
        self.assertEqual(submitted.status, DatasetProposal.Status.PENDING_REVIEW)
        self.assertIsNotNone(submitted.submitted_at)
        self.assertTrue(submitted.activity.filter(action="resubmitted").exists())

    def test_draft_content_edits_are_audited_with_before_and_after_values(self):
        updated = update_draft_proposal(
            self.proposal, self.owner,
            proposed_metadata={"title": "Lifecycle dataset, corrected"},
        )
        event = updated.activity.filter(action=DatasetProposalActivity.Action.CONTENT_UPDATED).latest("created_at")
        self.assertEqual(event.actor_id, self.owner.pk)
        self.assertIn("proposed_metadata", event.details["changed_fields"])
        self.assertEqual(event.details["before"]["proposed_metadata"], {})
        self.assertEqual(event.details["after"]["proposed_metadata"]["title"], "Lifecycle dataset, corrected")

    def test_non_submitter_cannot_reopen_requested_changes(self):
        from apps.datasets.services.proposals import request_proposal_changes, resubmit_requested_proposal
        submit_proposal(self.proposal, self.owner)
        request_proposal_changes(self.proposal, self.reviewers[0], "Please update the summary.")
        with self.assertRaises(UnauthorizedProposalReviewerError):
            resubmit_requested_proposal(self.proposal, self.reviewers[0])

    def test_submitter_can_submit_a_draft_proposal(self):
        submitted = submit_proposal(
            self.proposal, self.owner, ChangeType.PATCH
        )
        self.assertEqual(submitted.status, DatasetProposal.Status.PENDING_REVIEW)
        self.assertIsNotNone(submitted.submitted_at)

    def test_submitter_cannot_review_own_proposal(self):
        from apps.accounts.models import UserRole
        from apps.datasets.services.proposals import request_proposal_changes
        UserRole.objects.get_or_create(profile=self.owner.profile, role="reviewer")
        submit_proposal(self.proposal, self.owner)
        with self.assertRaises(UnauthorizedProposalReviewerError):
            submit_proposal_vote(self.proposal, self.owner, DatasetProposalVote.Vote.APPROVE)
        with self.assertRaises(UnauthorizedProposalReviewerError):
            request_proposal_changes(self.proposal, self.owner, "I reviewed my own work.")

    def test_non_reviewer_cannot_vote(self):
        submit_proposal(self.proposal, self.owner, ChangeType.PATCH)
        with self.assertRaises(UnauthorizedProposalReviewerError):
            submit_proposal_vote(
                self.proposal,
                self.owner,
                DatasetProposalVote.Vote.APPROVE,
            )

    def test_approval_is_derived_from_quorum_votes(self):
        submit_proposal(self.proposal, self.owner, ChangeType.PATCH)
        for reviewer in self.reviewers[:2]:
            result = submit_proposal_vote(
                self.proposal,
                reviewer,
                DatasetProposalVote.Vote.APPROVE,
            )
            self.assertEqual(result["status"], DatasetProposal.Status.PENDING_REVIEW)
        result = submit_proposal_vote(
            self.proposal,
            self.reviewers[2],
            DatasetProposalVote.Vote.APPROVE,
        )
        self.assertEqual(result["status"], DatasetProposal.Status.APPROVED)
        self.proposal.refresh_from_db()
        self.assertEqual(self.proposal.status, DatasetProposal.Status.APPROVED)

    def test_rejection_requires_a_reason_and_reviewer_majority(self):
        submit_proposal(self.proposal, self.owner, ChangeType.PATCH)
        with self.assertRaises(ValueError):
            submit_proposal_vote(
                self.proposal,
                self.reviewers[0],
                DatasetProposalVote.Vote.REJECT,
            )
        submit_proposal_vote(
            self.proposal,
            self.reviewers[0],
            DatasetProposalVote.Vote.REJECT,
            reason="The proposed changes are incomplete.",
        )
        submit_proposal_vote(
            self.proposal,
            self.reviewers[1],
            DatasetProposalVote.Vote.REJECT,
            reason="The proposed changes are incomplete.",
        )
        result = submit_proposal_vote(
            self.proposal,
            self.reviewers[2],
            DatasetProposalVote.Vote.APPROVE,
        )
        self.assertEqual(result["status"], DatasetProposal.Status.REJECTED)

    def test_raw_sql_cannot_update_a_recorded_vote(self):
        if connection.vendor != "postgresql":
            self.skipTest("PostgreSQL vote-history trigger requires PostgreSQL.")
        vote = DatasetProposalVote.objects.create(
            proposal=self.proposal,
            reviewer=self.reviewers[0],
            vote=DatasetProposalVote.Vote.APPROVE,
        )
        with self.assertRaises(DatabaseError):
            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute(
                        "UPDATE datasets_datasetproposalvote SET vote = 'reject' WHERE id = %s",
                        [vote.pk],
                    )

    def test_duplicate_reviewer_vote_is_rejected(self):
        submit_proposal(self.proposal, self.owner, ChangeType.PATCH)
        reviewer = self.reviewers[0]
        submit_proposal_vote(
            self.proposal, reviewer, DatasetProposalVote.Vote.APPROVE
        )
        with self.assertRaises(DuplicateProposalVoteError):
            submit_proposal_vote(
                self.proposal, reviewer, DatasetProposalVote.Vote.APPROVE
            )

    def test_direct_status_updates_cannot_approve_without_votes(self):
        submit_proposal(self.proposal, self.owner, ChangeType.PATCH)
        with self.assertRaises(RuntimeError):
            DatasetProposal.objects.filter(pk=self.proposal.pk).update(
                status=DatasetProposal.Status.APPROVED
            )
        with self.assertRaises(RuntimeError):
            self.proposal.status = DatasetProposal.Status.APPROVED
            self.proposal.save(update_fields=["status"])
        with self.assertRaises(RuntimeError):
            DatasetProposal.objects.transition(
                self.proposal.pk, DatasetProposal.Status.APPROVED
            )

    def test_submitted_proposal_manifest_is_immutable(self):
        DatasetProposalFile.objects.create(
            proposal=self.proposal,
            stored_object=self.stored_object,
            logical_file_id=uuid.uuid4(),
        )
        submit_proposal(self.proposal, self.owner, ChangeType.PATCH)
        with self.assertRaises(RuntimeError):
            self.proposal.files.all().delete()
        with self.assertRaises(RuntimeError):
            DatasetProposalFile.objects.create(
                proposal=self.proposal,
                stored_object=self.stored_object,
                logical_file_id=uuid.uuid4(),
            )

    def test_draft_can_be_cancelled_but_published_cannot(self):
        cancelled = cancel_proposal(self.proposal, self.owner)
        self.assertEqual(cancelled.status, DatasetProposal.Status.CANCELLED)
        with self.assertRaises(ProposalLifecycleError):
            cancel_proposal(cancelled, self.owner)

    def test_submission_computes_minimum_from_manifest_not_caller_input(self):
        # The proposal has an empty proposed manifest while the base release has
        # one file, so the actual removal requires MAJOR even when a caller asks
        # the service to accept PATCH.
        submitted = submit_proposal(self.proposal, self.owner, ChangeType.PATCH)
        self.assertEqual(submitted.status, DatasetProposal.Status.PENDING_REVIEW)
        self.assertEqual(submitted.computed_min_change_type, ChangeType.MAJOR)
        self.assertIsNotNone(submitted.submitted_at)

    def test_postgres_trigger_requires_audited_feedback_for_changes_requested(self):
        from django.db import connection, IntegrityError, transaction
        from django.db.models import QuerySet
        if connection.vendor != "postgresql":
            self.skipTest("PostgreSQL trigger behavior requires PostgreSQL.")
        submit_proposal(self.proposal, self.owner)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                QuerySet.update(
                    DatasetProposal.objects.filter(pk=self.proposal.pk),
                    status=DatasetProposal.Status.CHANGES_REQUESTED,
                    decided_at=timezone.now(), last_activity_at=timezone.now(),
                )

    def test_postgres_trigger_allows_activity_timestamp_only_update(self):
        from django.db import connection
        if connection.vendor != "postgresql":
            self.skipTest("PostgreSQL trigger behavior requires PostgreSQL.")
        submit_proposal(self.proposal, self.owner, ChangeType.PATCH)
        timestamp = timezone.now()
        DatasetProposal.objects.filter(pk=self.proposal.pk).update(
            last_activity_at=timestamp
        )
        self.proposal.refresh_from_db()
        self.assertEqual(self.proposal.last_activity_at, timestamp)

def test_postgres_trigger_rejects_same_status_content_update(self):
    from django.db import connection, IntegrityError, transaction
    from django.db.models import QuerySet

    if connection.vendor != "postgresql":
        self.skipTest("PostgreSQL trigger behavior requires PostgreSQL.")

    submit_proposal(self.proposal, self.owner, ChangeType.PATCH)

    queryset = DatasetProposal.objects.filter(pk=self.proposal.pk)

    with self.assertRaises(IntegrityError):
        with transaction.atomic():
            QuerySet.update(
                queryset,
                change_summary="unauthorized mutation",
            )