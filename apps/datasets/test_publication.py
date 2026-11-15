"""
apps/datasets/test_publication.py

Run:
  docker exec ordp-backend-django-1 python manage.py test apps.datasets.test_publication -v 2
"""
import uuid
from threading import Event, Thread
from time import sleep
from unittest.mock import patch

from django.db import IntegrityError, close_old_connections, connection, transaction
from django.db.models import ProtectedError
from django.test import TestCase, TransactionTestCase
from django.utils import timezone

from apps.datasets.factories import make_user
from apps.datasets.models import (
    ChangeType, Dataset, DatasetFile, DatasetProposal, DatasetProposalFile,
    DatasetProposalVote,
    DatasetRelease, DatasetReleaseFile, StoredObject, MetadataEditAudit,
    ContributorAttribution,
)
from apps.datasets.services.publication import (
    EmptyDatasetError, InvalidProposalMetadataError, InvalidStoredObjectError,
    DatasetNotPublishableError, PublicationIntegrityError, ProposalNotApprovedError,
    StaleProposalError, ReleaseAlreadyWithdrawnError, V1ContentConflictError,
    publish_initial_dataset, publish_proposal, create_dataset_proposal,
    add_proposal_file, replace_proposal_file, remove_proposal_file,
    withdraw_release, materialize_proposed_metadata,
)
from apps.datasets.services.versioning import InvalidChangeTypeError
from apps.datasets.services.storage import StorageVerificationError
from apps.datasets.services.proposals import (
    cancel_proposal,
    submit_proposal,
    submit_proposal_vote,
)


def make_stored_object(dataset, name, verified=True, checksum=None, size=10):
    return StoredObject.objects.create(
        dataset=dataset,
        storage_key=f"objects/{dataset.id}/{uuid.uuid4()}/{name}",
        original_filename=name, file_type="csv", file_size=size,
        checksum_sha256=checksum or ("a" * 64),
        verified_at=timezone.now() if verified else None,
    )


def make_dataset(owner, names=("a.csv",), verified=True, status=Dataset.Status.PUBLISHED):
    dataset = Dataset.objects.create(title="Pub DS", owner=owner, status=status)
    for name in names:
        so = make_stored_object(dataset, name, verified)
        DatasetFile.objects.create(
            dataset=dataset, file_key=so.storage_key, original_filename=name,
            file_type="csv", file_size=10, checksum="a" * 64, stored_object=so,
        )
    return dataset




def make_proposal(
    dataset,
    release,
    metadata=None,
    computed="minor",
    declared="minor",
    status=DatasetProposal.Status.APPROVED,
    owner=None,
    copy_manifest=True,
):
    import copy

    owner = owner or dataset.owner

    # Start with the actual published metadata and make a genuine change.
    if metadata is None:
        metadata = {
            "description": (
                release.metadata_snapshot.get("description", "")
                + " Updated for publication test."
            )
        }

    proposal = DatasetProposal.objects.create(
        dataset=dataset,
        base_release=release,
        source=DatasetProposal.Source.OWNER_EDIT,
        status=DatasetProposal.Status.DRAFT,
        proposed_metadata=copy.deepcopy(metadata),
        declared_change_type=declared,
        computed_min_change_type=computed,
        submitted_by=owner,
    )

    if copy_manifest and release:
        for rf in release.files.order_by("display_order"):
            DatasetProposalFile.objects.create(
                proposal=proposal,
                stored_object=rf.stored_object,
                logical_file_id=rf.logical_file_id,
                display_order=rf.display_order,
            )

    if status != DatasetProposal.Status.DRAFT:
        proposal = submit_proposal(proposal, owner, computed)
        if status == DatasetProposal.Status.APPROVED:
            proposal = approve_proposal(proposal)

    return proposal



def approve_proposal(proposal):
    if proposal.status == DatasetProposal.Status.DRAFT:
        # Test fixture only: ensure the draft contains a real metadata change.
        if not proposal.proposed_metadata:
            base_description = (
                proposal.base_release.metadata_snapshot.get("description", "")
            )
            proposal.proposed_metadata = {
                "description": (
                    base_description + " Updated for approval test."
                )
            }
            proposal.save(update_fields=["proposed_metadata"])

        proposal = submit_proposal(
            proposal,
            proposal.submitted_by,
            proposal.computed_min_change_type or ChangeType.MINOR,
        )

    reviewers = []
    for index in range(3):
        reviewer_id = uuid.uuid4().hex[:12]
        reviewers.append(make_user(
            f"proposalreviewer{reviewer_id}{index}",
            f"proposalreviewer{reviewer_id}{index}@aastu.edu.et",
            role="reviewer",
        ))

    for reviewer in reviewers:
        submit_proposal_vote(
            proposal,
            reviewer,
            DatasetProposalVote.Vote.APPROVE,
        )

    return DatasetProposal.objects.get(pk=proposal.pk)

class PublicationTestCase(TestCase):
    def setUp(self):
        verifier = patch(
            "apps.datasets.services.publication.verify_stored_object_in_storage"
        )
        self.verify_stored_object = verifier.start()
        self.addCleanup(verifier.stop)


class InitialPublicationTests(PublicationTestCase):
    def setUp(self):
        super().setUp()
        self.owner = make_user("ipowner", "ipowner@aastu.edu.et")

    def test_creates_1_0_0_with_full_manifest_and_pointer(self):
        dataset = make_dataset(self.owner, ("a.csv", "b.csv"))
        release = publish_initial_dataset(dataset, self.owner)
        dataset.refresh_from_db()
        self.assertEqual(release.version, "1.0.0")
        self.assertIsNone(release.base_release)
        self.assertEqual(dataset.published_release_id, release.id)
        self.assertEqual(release.files.count(), 2)
        logical_ids = set(release.files.values_list("logical_file_id", flat=True))
        self.assertEqual(logical_ids, set(dataset.files.values_list("id", flat=True)))
        self.assertEqual(release.persistent_identifier, f"ordp:{dataset.id}:v1.0.0")
        self.assertEqual(dataset.persistent_identifier, f"ordp:{dataset.id}")
        self.assertEqual(dataset.current_version.file_key, release.files.first().stored_object.storage_key)

    def test_citation_snapshot_derived_from_same_metadata_snapshot(self):
        dataset = make_dataset(self.owner)
        release = publish_initial_dataset(dataset, self.owner)
        self.assertEqual(release.citation_snapshot["title"], release.metadata_snapshot["title"])
        self.assertEqual(
            release.citation_snapshot["doi_citation"],
            release.metadata_snapshot["citation"]["doi_citation"],
        )
        self.assertEqual(release.snapshot_schema_version, 1)
        self.assertEqual(release.metadata_snapshot["schema_version"], 1)

    def test_published_metadata_snapshot_survives_later_live_dataset_edit(self):
        dataset = make_dataset(self.owner)
        release = publish_initial_dataset(dataset, self.owner)
        original_snapshot = release.metadata_snapshot.copy()

        dataset.title = "Later V1 title"
        dataset.save(update_fields=["title"])

        release.refresh_from_db()
        self.assertEqual(release.metadata_snapshot, original_snapshot)
        self.assertNotEqual(dataset.title, release.metadata_snapshot["title"])

    def test_retry_returns_same_release(self):
        dataset = make_dataset(self.owner)
        first = publish_initial_dataset(dataset, self.owner)
        second = publish_initial_dataset(dataset, self.owner)
        self.assertEqual(first.id, second.id)
        self.assertEqual(dataset.releases.count(), 1)

    def test_retry_withdrawn_initial_release_fails(self):
        dataset = make_dataset(self.owner)
        release = publish_initial_dataset(dataset, self.owner)
        withdraw_release(release, self.owner, reason="Initial defect")
        dataset.refresh_from_db()

        with self.assertRaises(DatasetNotPublishableError):
            publish_initial_dataset(dataset, self.owner)

    def test_withdrawing_initial_release_disables_legacy_current_version(self):
        dataset = make_dataset(self.owner)
        release = publish_initial_dataset(dataset, self.owner)
        withdraw_release(release, self.owner, reason="Initial defect")
        dataset.refresh_from_db()

        self.assertIsNone(dataset.published_release_id)
        self.assertIsNone(dataset.current_version)
        self.assertTrue(dataset.files.exists())
        self.assertEqual(release.files.count(), 1)

    def test_missing_stored_object_blocks_publication(self):
        dataset = make_dataset(self.owner)
        DatasetFile.objects.create(
            dataset=dataset, file_key="x", original_filename="x.csv",
            file_type="csv", file_size=1, checksum="b" * 64,
        )
        with self.assertRaises(InvalidStoredObjectError):
            publish_initial_dataset(dataset, self.owner)
        self.assertEqual(dataset.releases.count(), 0)

    def test_unverified_object_blocks_publication(self):
        dataset = make_dataset(self.owner, verified=False)
        with self.assertRaises(InvalidStoredObjectError):
            publish_initial_dataset(dataset, self.owner)

    def test_failed_storage_verification_blocks_initial_publication(self):
        dataset = make_dataset(self.owner)
        stored_object = dataset.files.first().stored_object
        self.verify_stored_object.side_effect = StorageVerificationError(
            "object checksum mismatch"
        )

        with self.assertRaises(InvalidStoredObjectError):
            publish_initial_dataset(dataset, self.owner)

        self.verify_stored_object.assert_called_once_with(
            stored_object, update_timestamp=False
        )
        self.assertEqual(dataset.releases.count(), 0)

    def test_invalid_sha256_checksum_rejected(self):
        dataset = make_dataset(self.owner, names=())
        so = make_stored_object(dataset, "bad.csv", verified=True)
        if connection.vendor == "postgresql":
            with self.assertRaises(IntegrityError):
                with transaction.atomic():
                    StoredObject.objects.filter(pk=so.pk).update(checksum_sha256="z" * 64)
            return
        StoredObject.objects.filter(pk=so.pk).update(checksum_sha256="z" * 64)
        so.refresh_from_db()
        DatasetFile.objects.create(
            dataset=dataset, file_key=so.storage_key, original_filename="bad.csv",
            file_type="csv", file_size=10, checksum="a" * 64, stored_object=so,
        )
        with self.assertRaises(InvalidStoredObjectError):
            publish_initial_dataset(dataset, self.owner)

    def test_cross_dataset_stored_object_rejected(self):
        dataset = make_dataset(self.owner)
        other = make_dataset(self.owner, names=())          # second dataset, no DatasetFiles
        foreign = make_stored_object(other, "z.csv")        # belongs to `other`, attached to nothing
        file_ = dataset.files.first()
        file_.stored_object = foreign                       # point the first dataset's file at it
        if connection.vendor == "postgresql":
            with self.assertRaises(IntegrityError):
                with transaction.atomic():
                    file_.save()
        else:
            file_.save()
            with self.assertRaises(PublicationIntegrityError):
                publish_initial_dataset(dataset, self.owner)

    def test_not_reviewed_dataset_rejected(self):
        dataset = make_dataset(self.owner, status=Dataset.Status.PENDING)
        with self.assertRaises(DatasetNotPublishableError):
            publish_initial_dataset(dataset, self.owner)

    def test_dataset_without_files_rejected(self):
        dataset = make_dataset(self.owner, names=())
        with self.assertRaises(EmptyDatasetError):
            publish_initial_dataset(dataset, self.owner)

    def test_strict_license_input_validation(self):
        dataset = make_dataset(self.owner)
        with self.assertRaises(ValueError):
            publish_initial_dataset(dataset, self.owner, license_data={"unknown_key": "val"})


class ProposalPublicationTests(PublicationTestCase):
    def setUp(self):
        super().setUp()
        self.owner = make_user("ppowner", "ppowner@aastu.edu.et")
        self.dataset = make_dataset(self.owner, ("water.csv", "locations.csv"))
        self.release_1 = publish_initial_dataset(self.dataset, self.owner)

    def test_successful_proposal_publication_attributes_submitter_to_resulting_release(self):
        proposal = make_proposal(self.dataset, self.release_1, owner=self.owner)

        release_2 = publish_proposal(proposal, self.owner, "minor")

        attribution = ContributorAttribution.objects.get(
            dataset=self.dataset,
            release=release_2,
            user=self.owner,
        )
        self.assertEqual(attribution.name, self.owner.profile.full_name)
        self.assertEqual(attribution.affiliation, self.owner.profile.affiliation)
        self.assertEqual(attribution.contribution_description, "")
        self.assertFalse(
            ContributorAttribution.objects.filter(release=self.release_1).exists()
        )

    def test_repeated_publication_does_not_duplicate_submitter_attribution(self):
        proposal = make_proposal(self.dataset, self.release_1, owner=self.owner)

        first = publish_proposal(proposal, self.owner, "minor")
        second = publish_proposal(proposal, self.owner, "minor")

        self.assertEqual(first.pk, second.pk)
        self.assertEqual(
            ContributorAttribution.objects.filter(
                dataset=self.dataset, release=first, user=self.owner
            ).count(),
            1,
        )

    def test_proposal_without_submitter_does_not_create_fabricated_attribution(self):
        proposal = create_dataset_proposal(
            dataset=self.dataset,
            base_release=self.release_1,
            submitted_by=None,
        )

        # Simulate an already-submitted legacy proposal with no submitter.
        # Do not use submit_proposal(): it correctly requires a submitter.
        DatasetProposal.objects.transition(
            proposal.pk,
            DatasetProposal.Status.PENDING_REVIEW,
            submitted_at=timezone.now(),
            computed_min_change_type=ChangeType.PATCH,
            last_activity_at=timezone.now(),
        )

        # Create the required reviewer approvals.
        for index in range(3):
            reviewer = make_user(
                f"nosubmitterreviewer{index}{uuid.uuid4().hex[:8]}",
                f"nosubmitterreviewer{index}{uuid.uuid4().hex[:8]}@aastu.edu.et",
                role="reviewer",
            )
            DatasetProposalVote.objects.create(
                proposal=proposal,
                reviewer=reviewer,
                vote=DatasetProposalVote.Vote.APPROVE,
                reason="",
            )

        DatasetProposal.objects.transition(
            proposal.pk,
            DatasetProposal.Status.APPROVED,
            decided_at=timezone.now(),
            last_activity_at=timezone.now(),
        )

        proposal.refresh_from_db()
        self.assertIsNone(proposal.submitted_by_id)

        release_2 = publish_proposal(proposal, self.owner, "patch")

        self.assertFalse(
            ContributorAttribution.objects.filter(release=release_2).exists()
        )
    def test_create_dataset_proposal_copies_base_release_manifest(self):
        proposal = create_dataset_proposal(
            dataset=self.dataset,
            base_release=self.release_1,
            submitted_by=self.owner,
        )
        self.assertEqual(proposal.files.count(), 2)
        manifest_objects = set(proposal.files.values_list("stored_object_id", flat=True))
        base_objects = set(self.release_1.files.values_list("stored_object_id", flat=True))
        self.assertEqual(manifest_objects, base_objects)

    def test_create_proposal_rejects_v1_manifest_drift(self):
        legacy_file = self.dataset.files.first()
        DatasetFile.objects.filter(pk=legacy_file.pk).update(file_key="v1-edited.csv")

        with self.assertRaises(V1ContentConflictError):
            create_dataset_proposal(
                dataset=self.dataset,
                base_release=self.release_1,
                submitted_by=self.owner,
            )

    def test_approved_proposal_manifest_rejects_model_and_queryset_mutations(self):
        proposal = create_dataset_proposal(
            dataset=self.dataset,
            base_release=self.release_1,
            submitted_by=self.owner,
        )
        approve_proposal(proposal)
        entry = proposal.files.first()

        entry.display_order += 1
        with self.assertRaises(RuntimeError):
            entry.save()
        with self.assertRaises(RuntimeError):
            entry.delete()
        with self.assertRaises(RuntimeError):
            proposal.files.filter(pk=entry.pk).update(display_order=10)
        with self.assertRaises(RuntimeError):
            proposal.files.filter(pk=entry.pk).delete()
        with self.assertRaises(RuntimeError):
            proposal.files.bulk_update([entry], ["display_order"])
        with self.assertRaises(RuntimeError):
            DatasetProposalFile.objects.create(
                proposal=proposal,
                stored_object=make_stored_object(self.dataset, "late.csv"),
                logical_file_id=uuid.uuid4(),
            )
        with self.assertRaises(ProtectedError):
            proposal.delete()
        with self.assertRaises(ProtectedError):
            DatasetProposal.objects.filter(pk=proposal.pk).delete()

    def test_submitted_proposal_content_rejects_model_and_queryset_mutations(self):
        proposal = create_dataset_proposal(
            dataset=self.dataset,
            base_release=self.release_1,
            submitted_by=self.owner,
        )
        proposal.proposed_metadata = {"description": "Original proposal description."}
        proposal.save(update_fields=["proposed_metadata"])
        approve_proposal(proposal)

        proposal.proposed_metadata = {"title": "Tampered"}
        with self.assertRaises(RuntimeError):
            proposal.save()
        with self.assertRaises(RuntimeError):
            DatasetProposal.objects.filter(pk=proposal.pk).update(
                proposed_metadata={"title": "Tampered"}
            )
        with self.assertRaises(RuntimeError):
            DatasetProposal.objects.bulk_update(
                [proposal], ["proposed_metadata"]
            )

        proposal.refresh_from_db()
        self.assertEqual(
            proposal.proposed_metadata,
            {"description": "Original proposal description."},
    )

    def test_application_layer_rejects_cross_dataset_objects_and_proposal_moves(self):
        proposal = create_dataset_proposal(
            dataset=self.dataset,
            base_release=self.release_1,
            submitted_by=self.owner,
        )
        entry = proposal.files.first()
        other_release = publish_initial_dataset(
            make_dataset(self.owner, ("other.csv",)), self.owner
        )
        other_proposal = DatasetProposal.objects.create(
            dataset=other_release.dataset,
            base_release=other_release,
            source=DatasetProposal.Source.OWNER_EDIT,
        )
        foreign_object = other_release.files.first().stored_object

        with self.assertRaises(RuntimeError):
            DatasetProposalFile.objects.create(
                proposal=proposal,
                stored_object=foreign_object,
                logical_file_id=uuid.uuid4(),
            )
        entry.proposal = other_proposal
        with self.assertRaises(RuntimeError):
            entry.save()
        with self.assertRaises(RuntimeError):
            DatasetProposalFile.objects.filter(pk=entry.pk).update(
                proposal=other_proposal
            )

    def test_replace_and_add_file_builds_complete_manifest(self):
        water = self.dataset.files.get(original_filename="water.csv")
        proposal = make_proposal(
            self.dataset,
            self.release_1,
            owner=self.owner,
            status=DatasetProposal.Status.DRAFT,
        )
        new_water = make_stored_object(self.dataset, "water_v2.csv")
        sensor = make_stored_object(self.dataset, "sensor.csv")

        replace_proposal_file(proposal, water.id, new_water)
        add_proposal_file(proposal, sensor)
        approve_proposal(proposal)

        release_2 = publish_proposal(proposal, self.owner, "major")

        self.dataset.refresh_from_db()
        self.assertEqual(release_2.version, "2.0.0")
        self.assertEqual(release_2.base_release_id, self.release_1.id)
        self.assertEqual(self.dataset.published_release_id, release_2.id)
        self.assertEqual(release_2.files.count(), 3)

        keys = {entry.stored_object_id for entry in release_2.files.all()}
        self.assertIn(new_water.id, keys)
        self.assertIn(sensor.id, keys)

        legacy_keys = set(
            self.dataset.files.values_list("file_key", flat=True)
        )
        self.assertEqual(
            legacy_keys,
            {
                entry.stored_object.storage_key
                for entry in release_2.files.all()
            },
        )

        # The original release remains immutable.
        self.assertEqual(self.release_1.files.count(), 2)
        self.assertNotIn(
            new_water.id,
            {
                entry.stored_object_id
                for entry in self.release_1.files.all()
            },
        )
    def test_remove_file_from_proposal_manifest(self):
        locations = self.dataset.files.get(original_filename="locations.csv")
        proposal = make_proposal(
            self.dataset, self.release_1, owner=self.owner, status=DatasetProposal.Status.DRAFT,
        )
        remove_proposal_file(proposal, locations.id)
        self.assertEqual(proposal.files.count(), 1)
        approve_proposal(proposal)

        release_2 = publish_proposal(proposal, self.owner, "major")
        self.assertEqual(release_2.files.count(), 1)
        self.assertEqual(release_2.version, "2.0.0")
        self.assertEqual(self.dataset.files.count(), 1)

    def test_v1_update_remains_downloadable_without_changing_published_release(self):
        release_file = self.release_1.files.first()
        published_key = release_file.stored_object.storage_key
        legacy_file = self.dataset.files.order_by("uploaded_at", "id").last()
        DatasetFile.objects.filter(pk=legacy_file.pk).update(
            file_key="legacy-updated-key",
            stored_object=None,
        )
        self.dataset.refresh_from_db()

        self.assertEqual(self.dataset.current_version.file_key, "legacy-updated-key")
        self.assertEqual(release_file.stored_object.storage_key, published_key)

    def test_empty_proposal_manifest_rejected(self):
        proposal = make_proposal(
            self.dataset,
            self.release_1,
            owner=self.owner,
            copy_manifest=False,
        )
        self.assertEqual(proposal.files.count(), 0)

        with self.assertRaises(EmptyDatasetError):
            publish_proposal(proposal, self.owner, "major")

        self.assertEqual(self.dataset.releases.count(), 1)
        proposal.refresh_from_db()
        self.assertEqual(proposal.status, DatasetProposal.Status.APPROVED)
    def test_metadata_applied_to_snapshot_not_live_dataset(self):
        proposal = make_proposal(
            self.dataset,
            self.release_1,
            {"title": "Renamed"},
            owner=self.owner,
        )

        release_2 = publish_proposal(proposal, self.owner, "major")

        self.dataset.refresh_from_db()

        self.assertEqual(release_2.version, "2.0.0")
        self.assertEqual(release_2.metadata_snapshot["title"], "Renamed")
        self.assertEqual(release_2.citation_snapshot["title"], "Renamed")

        # Publishing a release must not mutate the live V1 dataset row.
        self.assertEqual(self.dataset.title, "Pub DS")

        # The original published release must remain unchanged.
        self.release_1.refresh_from_db()
        self.assertEqual(
            self.release_1.metadata_snapshot["title"],
            "Pub DS",
        )

    def test_audited_direct_corrections_are_carried_into_the_next_release(self):
        from copy import deepcopy
        before = deepcopy(self.release_1.metadata_snapshot)
        after = deepcopy(before)
        after["description"] = "Audited correction before the next release"
        MetadataEditAudit.objects.create(
            dataset=self.dataset,
            actor=self.owner,
            changed_fields=["description"],
            before_snapshot=before,
            after_snapshot=after,
        )
        proposal = create_dataset_proposal(
            dataset=self.dataset,
            base_release=self.release_1,
            submitted_by=self.owner,
        )
        self.assertEqual(
            proposal.proposed_metadata["description"],
            "Audited correction before the next release",
        )
        approve_proposal(proposal)
        release = publish_proposal(proposal, self.owner, "patch")
        self.assertEqual(
            release.metadata_snapshot["description"],
            "Audited correction before the next release",
        )

    def test_optional_release_notes_are_copied_into_immutable_release(self):
        proposal = create_dataset_proposal(
            dataset=self.dataset,
            base_release=self.release_1,
            submitted_by=self.owner,
            change_summary="Internal review summary",
            release_notes="Public note describing this release.",
        )
        approve_proposal(proposal)
        release = publish_proposal(proposal, self.owner, "patch")
        self.assertEqual(release.change_summary, "Internal review summary")
        self.assertEqual(release.release_notes, "Public note describing this release.")

    def test_metadata_only_proposal_publishes_patch_release(self):
        proposal = make_proposal(
            self.dataset,
            self.release_1,
            {"description": "Corrected descriptive information."},
            declared="patch",
            owner=self.owner,
        )

        self.assertEqual(proposal.computed_min_change_type, "patch")

        release = publish_proposal(proposal, self.owner, "patch")

        self.assertEqual(release.version, "1.0.1")
        self.assertEqual(release.files.count(), self.release_1.files.count())
        self.assertEqual(
            release.metadata_snapshot["description"],
            "Corrected descriptive information.",
        )

    def test_unsupported_metadata_field_rejected_and_nothing_created(self):
        proposal = make_proposal(
            self.dataset,
            self.release_1,
            {"visibility": "public"},
            status=DatasetProposal.Status.DRAFT,
        )

        # Visibility is operational state, not semantic release metadata.
        # It must not be accepted as a proposed metadata change.
        with self.assertRaises(InvalidProposalMetadataError):
            submit_proposal(proposal, self.owner)

        self.assertEqual(self.dataset.releases.count(), 1)

        proposal.refresh_from_db()
        self.assertEqual(proposal.status, DatasetProposal.Status.DRAFT)

    def test_license_change_requires_reason(self):
        proposal = create_dataset_proposal(
            dataset=self.dataset,
            base_release=self.release_1,
            submitted_by=self.owner,
            proposed_license={
                "name": "MIT License",
                "identifier": "MIT",
                "url": "https://opensource.org/licenses/MIT",
            },
        )

        approve_proposal(proposal)

        # A license change without a reason must be rejected.
        with self.assertRaises(InvalidProposalMetadataError):
            publish_proposal(proposal, self.owner, "patch")

        # Cancel the invalid proposal before creating the corrected one.
        cancel_proposal(proposal, self.owner)

        corrected_proposal = create_dataset_proposal(
            dataset=self.dataset,
            base_release=self.release_1,
            submitted_by=self.owner,
            proposed_license=proposal.proposed_license,
            license_change_reason="Migrating to permissive open license.",
        )

        approve_proposal(corrected_proposal)

        release_2 = publish_proposal(
            corrected_proposal,
            self.owner,
            "patch",
        )

        self.assertEqual(release_2.license_snapshot["identifier"], "MIT")
        self.assertEqual(release_2.version, "1.0.1")
    def test_retry_is_idempotent(self):
        proposal = make_proposal(self.dataset, self.release_1)
        first = publish_proposal(proposal, self.owner, "minor")
        second = publish_proposal(proposal, self.owner, "minor")
        self.assertEqual(first.id, second.id)
        self.assertEqual(self.dataset.releases.count(), 2)

    def test_failed_legacy_sync_rolls_back_release_and_proposal(self):
        proposal = make_proposal(self.dataset, self.release_1, owner=self.owner)

        with patch(
            "apps.datasets.services.publication._sync_legacy_files",
            side_effect=RuntimeError("simulated legacy sync failure"),
        ):
            with self.assertRaisesRegex(RuntimeError, "simulated legacy sync failure"):
                publish_proposal(proposal, self.owner, "minor")

        self.assertEqual(self.dataset.releases.count(), 1)
        self.dataset.refresh_from_db()
        proposal.refresh_from_db()
        self.assertEqual(self.dataset.published_release_id, self.release_1.id)
        self.assertEqual(proposal.status, DatasetProposal.Status.APPROVED)
        self.assertIsNone(proposal.resulting_release_id)

    def test_reviewer_cannot_go_below_computed_minimum(self):
        base_title = self.release_1.metadata_snapshot.get("title") or "Dataset"

        proposal = make_proposal(
            self.dataset,
            self.release_1,
            metadata={"title": f"{base_title} - revised"},
            declared="minor",
        )

        # Confirm that the actual proposed change requires a MAJOR release.
        self.assertEqual(proposal.computed_min_change_type, "major")

        # A reviewer must not downgrade the computed MAJOR requirement to MINOR.
        with self.assertRaises(InvalidChangeTypeError):
            publish_proposal(proposal, self.owner, "minor")

        # A rejected publication must not create another release.
        self.assertEqual(self.dataset.releases.count(), 1)

    def test_reviewer_can_elevate(self):
        proposal = make_proposal(self.dataset, self.release_1, computed="patch", declared="patch")
        release = publish_proposal(proposal, self.owner, "major")
        self.assertEqual(release.version, "2.0.0")

    def test_unapproved_proposal_rejected(self):
        proposal = make_proposal(self.dataset, self.release_1, status=DatasetProposal.Status.PENDING_REVIEW)
        with self.assertRaises(ProposalNotApprovedError):
            publish_proposal(proposal, self.owner, "minor")

    def test_stale_proposal_marked_stale_and_not_published(self):
        first = make_proposal(self.dataset, self.release_1)
        publish_proposal(first, self.owner, "minor")  # dataset now on 1.1.0
        second = DatasetProposal.objects.create(
            dataset=self.dataset,
            base_release=self.release_1,
            source=DatasetProposal.Source.OWNER_EDIT,
            status=DatasetProposal.Status.DRAFT,
            submitted_by=self.owner,
            computed_min_change_type="minor",
            change_summary="Stale proposal created from an older release.",
        )
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE datasets_datasetproposal
                   SET status = %s,
                       submitted_at = NOW(),
                       computed_min_change_type = %s,
                       last_activity_at = NOW()
                 WHERE id = %s
                """,
                [DatasetProposal.Status.PENDING_REVIEW, "minor", second.pk],
            )
        reviewers = []
        for index in range(3):
            reviewer = make_user(
                f"stale-reviewer-{index}-{uuid.uuid4().hex[:6]}",
                f"stale-reviewer-{index}-{uuid.uuid4().hex[:6]}@aastu.edu.et",
                role="reviewer",
            )
            reviewers.append(reviewer)
        for reviewer in reviewers:
            DatasetProposalVote.objects.create(
                proposal=second,
                reviewer=reviewer,
                vote=DatasetProposalVote.Vote.APPROVE,
                reason="",
            )
        DatasetProposal.objects.transition(
            second.pk,
            DatasetProposal.Status.APPROVED,
            decided_at=timezone.now(),
            last_activity_at=timezone.now(),
        )
        with self.assertRaises(StaleProposalError):
            publish_proposal(second, self.owner, "minor")
        second.refresh_from_db()
        self.assertEqual(second.status, DatasetProposal.Status.STALE)
        self.assertEqual(self.dataset.releases.count(), 2)

    def test_v1_edit_after_proposal_marks_proposal_stale_without_overwriting_edit(self):
        proposal = create_dataset_proposal(
            dataset=self.dataset,
            base_release=self.release_1,
            submitted_by=self.owner,
            declared_change_type="minor",
        )
        proposal.computed_min_change_type = "minor"
        approve_proposal(proposal)
        legacy_file = self.dataset.files.first()
        edited_key = "v1-edited-after-proposal.csv"
        DatasetFile.objects.filter(pk=legacy_file.pk).update(file_key=edited_key)

        with self.assertRaises(V1ContentConflictError):
            publish_proposal(proposal, self.owner, "minor")

        proposal.refresh_from_db()
        self.assertEqual(proposal.status, DatasetProposal.Status.STALE)
        self.assertEqual(DatasetFile.objects.get(pk=legacy_file.pk).file_key, edited_key)
        self.assertEqual(self.dataset.releases.count(), 1)

    def test_cannot_publish_proposal_on_withdrawn_base_release(self):
        proposal = make_proposal(self.dataset, self.release_1)
        withdraw_release(self.release_1, self.owner, reason="Flawed base data")
        self.release_1.refresh_from_db()
        with self.assertRaises(StaleProposalError):
            publish_proposal(proposal, self.owner, "minor")
        proposal.refresh_from_db()
        self.assertEqual(proposal.status, DatasetProposal.Status.STALE)

    def test_proposal_snapshot_schema_mismatch_is_rejected(self):
        with self.assertRaises(PublicationIntegrityError):
            materialize_proposed_metadata({"schema_version": 2}, {})


class PostgreSQLConcurrentPublicationTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        verifier = patch(
            "apps.datasets.services.publication.verify_stored_object_in_storage"
        )
        self.verify_stored_object = verifier.start()
        self.addCleanup(verifier.stop)
        self.owner = make_user("concurrentpubowner", "concurrentpubowner@aastu.edu.et")
        self.dataset = make_dataset(self.owner)
        self.release_1 = publish_initial_dataset(self.dataset, self.owner)
        self.proposal = make_proposal(
            self.dataset, self.release_1, owner=self.owner
        )

    def test_concurrent_publication_retries_return_one_release(self):
        if connection.vendor != "postgresql":
            self.skipTest("Publication locking requires PostgreSQL.")

        start = Event()
        results = []
        errors = []

        def publish():
            close_old_connections()
            try:
                if not start.wait(timeout=10):
                    raise TimeoutError("Timed out waiting to start concurrent publication.")
                proposal = DatasetProposal.objects.get(pk=self.proposal.pk)
                release = publish_proposal(proposal, self.owner, "minor")
                results.append(release.pk)
            except Exception as exc:
                errors.append(exc)
            finally:
                close_old_connections()

        threads = [Thread(target=publish) for _ in range(2)]
        for thread in threads:
            thread.start()
        start.set()
        for thread in threads:
            thread.join(timeout=30)

        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(errors, [])
        self.assertEqual(len(results), 2)
        self.assertEqual(results[0], results[1])
        self.assertEqual(self.dataset.releases.count(), 2)
        self.proposal.refresh_from_db()
        self.assertEqual(self.proposal.status, DatasetProposal.Status.PUBLISHED)


class PostgreSQLInitialSnapshotConsistencyTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        verifier = patch(
            "apps.datasets.services.publication.verify_stored_object_in_storage"
        )
        verifier.start()
        self.addCleanup(verifier.stop)
        from apps.accounts.models import UserProfile
        from apps.datasets.models import Contributor
        from apps.metadata.models import Category, Language, Metadata

        self.UserProfile = UserProfile
        self.Contributor = Contributor
        self.Metadata = Metadata
        self.Language = Language
        self.owner = make_user("snapshot-race-owner", "snapshot-race-owner@aastu.edu.et")
        self.contributor_user = make_user(
            "snapshot-race-contributor", "snapshot-race-contributor@aastu.edu.et"
        )
        self.dataset = make_dataset(self.owner, ("snapshot.csv",))
        category = Category.objects.create(name="Snapshot race category")
        self.metadata = Metadata.objects.create(
            dataset=self.dataset,
            description="Before description",
            category=category,
        )
        self.old_language = Language.objects.create(name="Snapshot old language")
        self.new_language = Language.objects.create(name="Snapshot new language")
        self.metadata.languages.add(self.old_language)
        self.contributor = self.Contributor.objects.create(
            dataset=self.dataset,
            user=self.contributor_user,
            name="Contributor before",
            contributor_type=self.Contributor.ContributorType.CO_AUTHOR,
        )
        self.owner.profile.full_name = "Owner before"
        self.owner.profile.save(update_fields=["full_name"])

    def test_metadata_m2m_contributor_and_profile_writes_wait_for_snapshot(self):
        if connection.vendor != "postgresql":
            self.skipTest("Initial publication row-lock behavior requires PostgreSQL.")

        snapshot_locked = Event()
        continue_snapshot = Event()
        writer_started = Event()
        releases = []
        errors = []

        from apps.datasets.services.snapshots import generate_metadata_snapshot as generate

        def pause_snapshot(dataset, **kwargs):
            snapshot_locked.set()
            if not continue_snapshot.wait(timeout=10):
                raise TimeoutError("Timed out waiting to release snapshot capture.")
            return generate(dataset, **kwargs)

        def publish():
            close_old_connections()
            try:
                with patch(
                    "apps.datasets.services.publication.generate_metadata_snapshot",
                    side_effect=pause_snapshot,
                ):
                    releases.append(publish_initial_dataset(self.dataset, self.owner))
            except Exception as exc:
                errors.append(exc)
            finally:
                close_old_connections()

        def update_v1_sources():
            close_old_connections()
            try:
                writer_started.set()
                with transaction.atomic():
                    dataset = Dataset.objects.select_for_update().get(
                        pk=self.dataset.pk
                    )
                    dataset.title = "After title"
                    dataset.save(update_fields=["title"])
                    metadata = self.Metadata.objects.select_for_update().get(
                        dataset=dataset
                    )
                    metadata.description = "After description"
                    metadata.save(update_fields=["description"])
                    metadata.languages.set([self.new_language])
                    contributor = self.Contributor.objects.select_for_update().get(
                        pk=self.contributor.pk
                    )
                    contributor.name = "Contributor after"
                    contributor.save(update_fields=["name"])
                    profile = self.UserProfile.objects.select_for_update().get(
                        user_id=self.owner.pk
                    )
                    profile.full_name = "Owner after"
                    profile.save(update_fields=["full_name"])
            except Exception as exc:
                errors.append(exc)
            finally:
                close_old_connections()

        publisher = Thread(target=publish)
        publisher.start()
        self.assertTrue(snapshot_locked.wait(timeout=10))
        writer = Thread(target=update_v1_sources)
        writer.start()
        self.assertTrue(writer_started.wait(timeout=10))
        sleep(0.1)
        continue_snapshot.set()
        publisher.join(timeout=20)
        writer.join(timeout=20)

        self.assertFalse(publisher.is_alive())
        self.assertFalse(writer.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(len(releases), 1)
        snapshot = releases[0].metadata_snapshot
        self.assertEqual(snapshot["title"], "Pub DS")
        self.assertEqual(snapshot["description"], "Before description")
        self.assertEqual(snapshot["languages"], ["Snapshot old language"])
        self.assertEqual(snapshot["creator"]["name"], "Owner before")
        self.assertEqual(snapshot["contributors"][0]["name"], "Contributor before")
        self.dataset.refresh_from_db()
        self.assertEqual(self.dataset.title, "After title")


class ImmutabilityAndWithdrawalTests(PublicationTestCase):
    def setUp(self):
        super().setUp()
        self.owner = make_user("iwowner", "iwowner@aastu.edu.et")
        self.dataset = make_dataset(self.owner, ("file1.csv",))
        self.release_1 = publish_initial_dataset(self.dataset, self.owner)

    def test_release_cannot_be_deleted(self):
        with self.assertRaises((RuntimeError, ProtectedError)):
            self.release_1.delete()

    def test_release_fields_cannot_be_modified(self):
        self.release_1.major = 99
        with self.assertRaises(RuntimeError):
            self.release_1.save()

    def test_release_file_cannot_be_deleted_or_modified(self):
        rf = self.release_1.files.first()
        with self.assertRaises((RuntimeError, ProtectedError)):
            rf.delete()

        rf.display_order = 100
        with self.assertRaises(RuntimeError):
            rf.save()

    def test_withdraw_release_lifecycle(self):
        # Publish a second release so we have 1.0.0 and 1.1.0
        proposal = make_proposal(self.dataset, self.release_1, owner=self.owner)
        release_2 = publish_proposal(proposal, self.owner, "minor")
        self.dataset.refresh_from_db()
        self.assertEqual(self.dataset.published_release_id, release_2.id)

        # Create an active proposal based on release_2
        active_prop = DatasetProposal.objects.create(
            dataset=self.dataset, base_release=release_2,
            source=DatasetProposal.Source.OWNER_EDIT, status=DatasetProposal.Status.DRAFT,
            submitted_by=self.owner,
        )

        # Withdraw release_2
        withdraw_release(release_2, self.owner, reason="Critical data error discovered.")
        release_2.refresh_from_db()
        self.assertEqual(release_2.status, DatasetRelease.Status.WITHDRAWN)
        self.assertEqual(release_2.withdrawal_reason, "Critical data error discovered.")
        self.assertIsNotNone(release_2.withdrawn_at)
        self.assertEqual(release_2.withdrawn_by_id, self.owner.id)

        # Dataset published_release pointer rolls back to release_1
        self.dataset.refresh_from_db()
        self.assertEqual(self.dataset.published_release_id, self.release_1.id)
        self.assertEqual(
            self.dataset.current_version.file_key,
            self.release_1.files.first().stored_object.storage_key,
        )

        # Active proposal based on withdrawn release becomes STALE
        active_prop.refresh_from_db()
        self.assertEqual(active_prop.status, DatasetProposal.Status.STALE)

        # Cannot withdraw again
        with self.assertRaises(ReleaseAlreadyWithdrawnError):
            withdraw_release(release_2, self.owner, reason="Second try")

    def test_publish_after_withdrawal_uses_next_unused_semver(self):
        proposal_2 = make_proposal(self.dataset, self.release_1, owner=self.owner)
        release_2 = publish_proposal(proposal_2, self.owner, "minor")
        withdraw_release(release_2, self.owner, reason="Withdrawn test release")

        proposal_3 = make_proposal(
            self.dataset, self.release_1, owner=self.owner, computed="minor"
        )
        release_3 = publish_proposal(proposal_3, self.owner, "minor")

        self.assertEqual(release_3.version, "1.2.0")
        self.assertEqual(release_3.base_release_id, self.release_1.id)
        self.assertEqual(
            set(self.dataset.releases.values_list("major", "minor", "patch")),
            {(1, 0, 0), (1, 1, 0), (1, 2, 0)},
        )
