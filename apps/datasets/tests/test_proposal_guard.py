"""Tests for the proposal lifecycle guard trigger (migration 0034).
These tests run against the test PostgreSQL database used by Django's test runner.
"""

import datetime
from django.db import IntegrityError, transaction
from django.test import TransactionTestCase
from django.utils import timezone

from apps.datasets.models import Dataset, DatasetRelease, DatasetProposal
from apps.datasets.models import DatasetProposalStatus  # assuming an enum or choices

class ProposalLifecycleGuardTests(TransactionTestCase):
    def setUp(self):
        # Create a minimal Dataset needed for proposals and releases
        self.dataset = Dataset.objects.create(
            title="Test Dataset",
            description="A dataset for testing",
            created_at=timezone.now(),
            owner_id=1,  # assuming a simple integer FK; adjust if needed
        )
        # Create a draft proposal
        self.proposal = DatasetProposal.objects.create(
            dataset=self.dataset,
            title="Draft Proposal",
            description="Draft description",
            status=DatasetProposalStatus.DRAFT,
            created_at=timezone.now(),
            updated_at=timezone.now(),
        )

    def test_same_status_protected_fields_cannot_change(self):
        """Changing lifecycle fields without a status transition must fail."""
        # Move to pending_review (valid transition)
        self.proposal.status = DatasetProposalStatus.PENDING_REVIEW
        self.proposal.submitted_at = timezone.now()
        self.proposal.save()
        # Attempt to modify a protected field while status stays PENDING_REVIEW
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                DatasetProposal.objects.filter(pk=self.proposal.pk).update(
                    submitted_at=timezone.now()  # change same field
                )

    def test_same_status_last_activity_at_allowed(self):
        """Updating last_activity_at on same status should succeed."""
        # Transition to pending_review first
        self.proposal.status = DatasetProposalStatus.PENDING_REVIEW
        self.proposal.submitted_at = timezone.now()
        self.proposal.save()
        # Update a benign field – assume `last_activity_at` exists on the model
        try:
            DatasetProposal.objects.filter(pk=self.proposal.pk).update(
                last_activity_at=timezone.now()
            )
        except IntegrityError:
            self.fail("IntegrityError raised when updating allowed field last_activity_at")

    def test_approved_to_published_transition_successful(self):
        """A valid approved → published transition should succeed."""
        # Create a release that will be the resulting release
        release = DatasetRelease.objects.create(
            dataset=self.dataset,
            version="v1.0",
            published_at=timezone.now(),
            created_at=timezone.now(),
        )
        # Move proposal to approved
        self.proposal.status = DatasetProposalStatus.APPROVED
        self.proposal.approved_at = timezone.now()
        self.proposal.save()
        # Now publish
        self.proposal.status = DatasetProposalStatus.PUBLISHED
        self.proposal.published_at = timezone.now()
        self.proposal.resulting_release = release
        self.proposal.final_change_type = "major"  # assuming a string field
        self.proposal.save()
        # Verify fields persisted
        refreshed = DatasetProposal.objects.get(pk=self.proposal.pk)
        self.assertEqual(refreshed.status, DatasetProposalStatus.PUBLISHED)
        self.assertIsNotNone(refreshed.resulting_release)

    def test_approved_to_published_invalid_release_fails(self):
        """Publishing with an invalid release reference must fail."""
        # Move proposal to approved
        self.proposal.status = DatasetProposalStatus.APPROVED
        self.proposal.approved_at = timezone.now()
        self.proposal.save()
        # Attempt to publish with a non‑existent release ID
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                self.proposal.status = DatasetProposalStatus.PUBLISHED
                self.proposal.published_at = timezone.now()
                self.proposal.resulting_release_id = 9999  # does not exist
                self.proposal.final_change_type = "major"
                self.proposal.save()
