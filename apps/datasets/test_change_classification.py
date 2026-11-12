
import uuid

from django.test import TestCase

from apps.datasets.factories import make_user
from apps.datasets.models import (
    ChangeType,
    Dataset,
    DatasetProposal,
    DatasetProposalFile,
    DatasetRelease,
    DatasetReleaseFile,
    StoredObject,
)
from apps.datasets.services.change_classification import (
    classify_proposal,
    classify_proposal_changes,
)


class ProposalChangeClassificationTests(TestCase):
    def setUp(self):
        self.owner = make_user(
            "classifier-owner",
            "classifier-owner@aastu.edu.et",
        )

        self.dataset = Dataset.objects.create(
            title="Classifier dataset",
            owner=self.owner,
            status=Dataset.Status.PUBLISHED,
        )

        self.obj = StoredObject.objects.create(
            dataset=self.dataset,
            storage_key=(
                f"objects/{self.dataset.pk}/{uuid.uuid4()}/base.csv"
            ),
            original_filename="base.csv",
            file_type="csv",
            file_size=10,
            checksum_sha256="a" * 64,
        )

        self.release = DatasetRelease.objects.create(
            dataset=self.dataset,
            major=1,
            minor=0,
            patch=0,
            metadata_snapshot={
                "schema_version": 1,
                "title": "Classifier dataset",
                "description": "Original description",
                "keywords": ["water"],
                "category": {
                    "id": "cat-1",
                    "name": "Water",
                    "description": "",
                },
                "creator": {
                    "name": "Researcher",
                    "affiliation": "AASTU",
                },
                "contributors": [],
                "coverage": {
                    "geographic": "Ethiopia",
                    "temporal": "2020",
                },
                "methodology": {
                    "collection_method": "survey",
                    "instances_represent": "households",
                    "data_preprocessing": "none",
                },
                "resources": {
                    "related_resources": "",
                },
            },
        )

        DatasetReleaseFile.objects.create(
            release=self.release,
            stored_object=self.obj,
            logical_file_id=uuid.uuid4(),
        )

        self.release.status = DatasetRelease.Status.PUBLISHED
        self.release.save(update_fields=["status"])

        self.dataset.published_release = self.release
        self.dataset.save(update_fields=["published_release"])

        # Classification tests must work with editable draft proposals.
        self.proposal = DatasetProposal.objects.create(
            dataset=self.dataset,
            base_release=self.release,
            source=DatasetProposal.Source.OWNER_EDIT,
            submitted_by=self.owner,
            status=DatasetProposal.Status.DRAFT,
            submitted_at=None,
        )

        base_entry = self.release.files.first()

        DatasetProposalFile.objects.create(
            proposal=self.proposal,
            stored_object=self.obj,
            logical_file_id=base_entry.logical_file_id,
        )

    def set_metadata(self, metadata):
        self.proposal.proposed_metadata = metadata
        self.proposal.save(update_fields=["proposed_metadata"])
        self.proposal.refresh_from_db()

    def test_descriptive_correction_is_patch(self):
        self.set_metadata({
            "description": "Corrected description",
        })

        self.assertEqual(
            classify_proposal_changes(self.proposal),
            ChangeType.PATCH,
        )

    def test_adding_keyword_is_minor(self):
        self.set_metadata({
            "keywords": ["water", "climate"],
        })

        self.assertEqual(
            classify_proposal_changes(self.proposal),
            ChangeType.MINOR,
        )

    def test_title_change_is_major(self):
        self.set_metadata({
            "title": "A different research subject",
        })

        self.assertEqual(
            classify_proposal_changes(self.proposal),
            ChangeType.MAJOR,
        )

    def test_category_change_is_major(self):
        self.set_metadata({
            "category": {
                "id": "cat-2",
                "name": "Climate",
                "description": "Climate research",
            },
        })

        self.assertEqual(
            classify_proposal_changes(self.proposal),
            ChangeType.MAJOR,
        )

    def test_category_and_contributor_attribution_changes_are_supported_and_major(self):
        self.set_metadata({
            "category": {
                "id": "cat-2",
                "name": "Climate",
                "description": "Climate research",
            },
            "contributors": [
                {
                    "name": "New Coauthor",
                    "contributor_type": "co_author",
                    "order": 1,
                    "affiliation": "AASTU",
                },
            ],
        })

        self.assertEqual(
            classify_proposal_changes(self.proposal),
            ChangeType.MAJOR,
        )

    def test_highest_severity_wins_when_patch_minor_and_major_changes_are_combined(self):
        self.set_metadata({
            "title": "Renamed research subject",
            "description": "Typo correction",
            "keywords": ["water", "climate"],
        })

        self.assertEqual(
            classify_proposal_changes(self.proposal),
            ChangeType.MAJOR,
        )

    def test_removed_file_is_major(self):
        self.proposal.files.all().delete()

        self.assertEqual(
            classify_proposal_changes(self.proposal),
            ChangeType.MAJOR,
        )

    def test_formatting_only_description_change_is_patch_and_can_bypass_review(self):
        self.set_metadata({
            "description": "Original  description!",
        })

        result = classify_proposal(self.proposal)

        self.assertEqual(
            result.minimum_change_type,
            ChangeType.PATCH,
        )
        self.assertFalse(result.review_required)
        self.assertEqual(
            result.review_bypass_reason,
            "formatting_only_descriptive_patch",
        )

    def test_spelling_or_word_change_in_description_still_requires_review(self):
        self.set_metadata({
            "description": "Corrected description",
        })

        result = classify_proposal(self.proposal)

        self.assertEqual(
            result.minimum_change_type,
            ChangeType.PATCH,
        )
        self.assertTrue(result.review_required)
        self.assertEqual(result.review_bypass_reason, "")

    def test_creator_affiliation_change_is_major(self):
        self.set_metadata({
            "creator.affiliation": "Different institution",
        })

        self.assertEqual(
            classify_proposal_changes(self.proposal),
            ChangeType.MAJOR,
        )

    def test_no_op_proposal_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "no changes"):
            classify_proposal(self.proposal)

    def test_unknown_citation_snapshot_field_is_conservatively_major(self):
        from apps.datasets.services.change_classification import _rank_metadata_change
        self.assertEqual(
            _rank_metadata_change("citation.future_unclassified_field", "old", "new"),
            2,
        )

    def test_unknown_metadata_snapshot_field_is_conservatively_major(self):
        from apps.datasets.services.change_classification import _rank_metadata_change
        self.assertEqual(_rank_metadata_change("future_metadata_field", None, "value"), 2)

    def test_explicit_null_added_to_missing_nested_field_is_a_change(self):
        # has_header is intentionally absent from this historical snapshot.
        self.set_metadata({"methodology.has_header": None})
        result = classify_proposal(self.proposal)
        self.assertIn("methodology.has_header", result.changed_metadata_paths)
        self.assertEqual(result.minimum_change_type, ChangeType.MAJOR)

    def test_keyword_removal_is_major(self):
        self.set_metadata({"keywords": []})
        self.assertEqual(classify_proposal_changes(self.proposal), ChangeType.MAJOR)

    def test_keyword_order_and_whitespace_normalization_is_noop(self):
        self.set_metadata({"keywords": [" water "]})
        with self.assertRaisesRegex(ValueError, "no changes"):
            classify_proposal(self.proposal)
