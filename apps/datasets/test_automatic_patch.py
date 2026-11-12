"""
Tests for the narrow automatic-publication PATCH exemption.

Run:
    docker compose exec django python manage.py test apps.datasets.test_automatic_patch -v 2
"""

from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from apps.datasets.models import ChangeType
from apps.datasets.services.change_classification import classify_proposal


def make_file(logical_file_id, stored_object_id):
    return SimpleNamespace(
        logical_file_id=logical_file_id,
        stored_object_id=stored_object_id,
    )


def make_file_manager(files=None):
    return SimpleNamespace(all=lambda: files or [])


def make_proposal(
    before_metadata=None,
    after_metadata=None,
    base_files=None,
    proposed_files=None,
    declared_change_type=None,
):
    before_metadata = before_metadata or {}
    after_metadata = after_metadata or before_metadata

    base_release = SimpleNamespace(
        metadata_snapshot=before_metadata,
        files=make_file_manager(base_files),
        status="published",
    )

    proposal = SimpleNamespace(
        base_release=base_release,
        proposed_metadata={},
        files=make_file_manager(proposed_files),
        declared_change_type=declared_change_type,
    )

    return proposal, after_metadata


class AutomaticPatchClassificationTests(SimpleTestCase):

    def classify(self, proposal, after_metadata):
        with patch(
            "apps.datasets.services.change_classification.materialize_proposed_metadata",
            return_value=after_metadata,
        ):
            return classify_proposal(proposal)

    def test_punctuation_only_description_change_bypasses_review(self):
        before = {
            "description": "Water quality data collected in Addis Ababa"
        }
        after = {
            "description": "Water quality data collected in Addis Ababa."
        }

        proposal, after_metadata = make_proposal(before, after)
        result = self.classify(proposal, after_metadata)

        self.assertEqual(result.minimum_change_type, ChangeType.PATCH)
        self.assertFalse(result.review_required)
        self.assertEqual(
            result.review_bypass_reason,
            "formatting_only_descriptive_patch",
        )
        self.assertEqual(result.changed_metadata_paths, ("description",))

    def test_spacing_only_description_change_bypasses_review(self):
        before = {"description": "Water quality data"}
        after = {"description": "Water   quality   data"}

        proposal, after_metadata = make_proposal(before, after)
        result = self.classify(proposal, after_metadata)

        self.assertEqual(result.minimum_change_type, ChangeType.PATCH)
        self.assertFalse(result.review_required)
        self.assertEqual(
            result.review_bypass_reason,
            "formatting_only_descriptive_patch",
        )

    def test_changed_words_in_description_require_review(self):
        before = {"description": "Water quality data"}
        after = {"description": "Water pollution data"}

        proposal, after_metadata = make_proposal(before, after)
        result = self.classify(proposal, after_metadata)

        self.assertEqual(result.minimum_change_type, ChangeType.PATCH)
        self.assertTrue(result.review_required)
        self.assertEqual(result.review_bypass_reason, "")

    def test_adding_scientific_metadata_requires_review(self):
        before = {"description": "Water quality data"}
        after = {
            "description": "Water quality data",
            "methodology": {"collection_method": "Laboratory testing"},
        }

        proposal, after_metadata = make_proposal(before, after)
        result = self.classify(proposal, after_metadata)

        self.assertEqual(result.minimum_change_type, ChangeType.MAJOR)
        self.assertTrue(result.review_required)

    def test_title_change_requires_major_review(self):
        before = {"title": "Water Quality Dataset"}
        after = {"title": "Updated Water Quality Dataset"}

        proposal, after_metadata = make_proposal(before, after)
        result = self.classify(proposal, after_metadata)

        self.assertEqual(result.minimum_change_type, ChangeType.MAJOR)
        self.assertTrue(result.review_required)

    def test_unknown_metadata_path_defaults_to_major_review(self):
        before = {"description": "Water quality data"}
        after = {
            "description": "Water quality data",
            "new_metadata_field": "New value",
        }

        proposal, after_metadata = make_proposal(before, after)
        result = self.classify(proposal, after_metadata)

        self.assertEqual(result.minimum_change_type, ChangeType.MAJOR)
        self.assertTrue(result.review_required)

    def test_added_file_requires_review(self):
        before = {"description": "Water quality data"}
        proposal, after_metadata = make_proposal(
            before,
            before,
            [],
            [make_file("file-1", "object-1")],
        )

        result = self.classify(proposal, after_metadata)

        self.assertEqual(result.minimum_change_type, ChangeType.MINOR)
        self.assertTrue(result.review_required)
        self.assertEqual(result.added_file_ids, ("file-1",))

    def test_removed_file_requires_major_review(self):
        before = {"description": "Water quality data"}
        proposal, after_metadata = make_proposal(
            before,
            before,
            [make_file("file-1", "object-1")],
            [],
        )

        result = self.classify(proposal, after_metadata)

        self.assertEqual(result.minimum_change_type, ChangeType.MAJOR)
        self.assertTrue(result.review_required)
        self.assertEqual(result.removed_file_ids, ("file-1",))

    def test_replaced_file_requires_major_review(self):
        before = {"description": "Water quality data"}
        proposal, after_metadata = make_proposal(
            before,
            before,
            [make_file("file-1", "object-1")],
            [make_file("file-1", "object-2")],
        )

        result = self.classify(proposal, after_metadata)

        self.assertEqual(result.minimum_change_type, ChangeType.MAJOR)
        self.assertTrue(result.review_required)
        self.assertEqual(result.replaced_file_ids, ("file-1",))

    def test_format_only_change_combined_with_file_change_requires_review(self):
        before = {"description": "Water quality data"}
        after = {"description": "Water quality data."}
        proposal, after_metadata = make_proposal(
            before,
            after,
            [],
            [make_file("file-1", "object-1")],
        )

        result = self.classify(proposal, after_metadata)

        self.assertEqual(result.minimum_change_type, ChangeType.MINOR)
        self.assertTrue(result.review_required)
        self.assertEqual(result.added_file_ids, ("file-1",))

    def test_declared_minor_change_type_prevents_automatic_bypass(self):
        before = {"description": "Water quality data"}
        after = {"description": "Water quality data."}
        proposal, after_metadata = make_proposal(
            before,
            after,
            declared_change_type=ChangeType.MINOR,
        )

        result = self.classify(proposal, after_metadata)

        self.assertEqual(result.minimum_change_type, ChangeType.PATCH)
        self.assertTrue(result.review_required)
        self.assertEqual(result.review_bypass_reason, "")

    def test_no_op_proposal_is_rejected(self):
        before = {"description": "Water quality data"}
        proposal, after_metadata = make_proposal(before, before)

        with self.assertRaisesRegex(ValueError, "no changes"):
            self.classify(proposal, after_metadata)
