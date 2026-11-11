"""
apps/datasets/services/publication.py

Central authority for creating immutable DatasetRelease rows and managing proposals.

  publish_initial_dataset(dataset, published_by, license_data=None, require_verified=True)
  create_dataset_proposal(dataset, base_release, submitted_by, ...)
  publish_proposal(proposal, published_by, final_change_type, require_verified=True)
  withdraw_release(release, withdrawn_by, reason)

Both run in one transaction with row locks (lock order: Dataset, then Proposal)
and never touch live metadata.
"""
import copy
import json
import uuid
from typing import Any, Dict, Optional

from django.db import models, transaction
from django.utils import timezone

from apps.datasets.models import (
    ChangeType,
    Dataset,
    DatasetProposal,
    DatasetProposalFile,
    DatasetProposalActivity,
    DatasetRelease,
    ResourceIdentifier,
    DatasetReleaseFile,
    ContributorAttribution,
)
from apps.datasets.services.file_integrity import (
    is_valid_sha256,
    verify_stored_object_in_storage,
)
from apps.datasets.services.snapshots import (
    CURRENT_SNAPSHOT_SCHEMA_VERSION,
    citation_from_metadata_snapshot,
    generate_license_snapshot,
    generate_metadata_snapshot,
    lock_metadata_snapshot_sources,
    current_published_metadata_snapshot,
)
from apps.datasets.services.versioning import (
    calculate_next_version,
    validate_change_type_authority,
)
from apps.datasets.services.storage import StorageVerificationError
from apps.datasets.services.pid import (
    ensure_release_identifiers,
    withdraw_release_identifiers,
)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class PublicationError(Exception):
    """Base class for every publication failure."""


class DatasetNotPublishableError(PublicationError):
    """Dataset has not passed review, is inactive, or is otherwise not eligible."""


class EmptyDatasetError(PublicationError):
    """Dataset or proposal manifest has no files, so a release manifest would be empty."""


class InvalidStoredObjectError(PublicationError):
    """A required file has a missing/invalid/unverified StoredObject."""


class PublicationIntegrityError(PublicationError):
    """Cross-dataset reference or inconsistent release/proposal state."""


class ProposalNotApprovedError(PublicationError):
    pass


class StaleProposalError(PublicationError):
    """Proposal's base release is no longer the current published release or is withdrawn."""


class V1ContentConflictError(PublicationError):
    """The live V1 file manifest differs from the proposal's base release."""


class InvalidProposalMetadataError(PublicationError):
    pass


class ReleaseAlreadyWithdrawnError(PublicationError):
    pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _validate_stored_object(stored_object, dataset, label, require_verified):
    if stored_object is None:
        raise InvalidStoredObjectError(f"{label}: no StoredObject is linked.")
    if stored_object.dataset_id != dataset.id:
        raise PublicationIntegrityError(
            f"{label}: StoredObject {stored_object.id} belongs to a different dataset."
        )
    if stored_object.file_size is None or stored_object.file_size < 0:
        raise InvalidStoredObjectError(
            f"{label}: StoredObject has invalid file size ({stored_object.file_size})."
        )
    checksum = stored_object.checksum_sha256 or ""
    if not is_valid_sha256(checksum):
        raise InvalidStoredObjectError(
            f"{label}: StoredObject has no valid SHA-256 checksum: '{checksum}'."
        )
    if require_verified and stored_object.verified_at is None:
        raise InvalidStoredObjectError(f"{label}: StoredObject has not been verified.")
    try:
        verify_stored_object_in_storage(stored_object, update_timestamp=False)
    except StorageVerificationError as exc:
        raise InvalidStoredObjectError(
            f"{label}: StoredObject failed storage verification: {exc}"
        ) from exc


def _check_unique_objects(manifest):
    seen = set()
    for _, stored_object in manifest:
        if stored_object.id in seen:
            raise PublicationIntegrityError(
                f"StoredObject {stored_object.id} appears twice in one manifest."
            )
        seen.add(stored_object.id)


def _create_release_files(release, manifest):
    DatasetReleaseFile.objects.bulk_create([
        DatasetReleaseFile(
            release=release,
            stored_object=stored_object,
            logical_file_id=logical_id,
            display_order=index,
        )
        for index, (logical_id, stored_object) in enumerate(manifest)
    ])


def _sync_legacy_files(dataset, manifest):
    """Keep the V1 file surface aligned with the latest V2 release."""
    from apps.datasets.models import DatasetFile

    existing = list(DatasetFile.objects.select_for_update().filter(dataset=dataset))
    for legacy_file in existing:
        if legacy_file.stored_object_id:
            legacy_file.stored_object = None
            legacy_file.save(update_fields=["stored_object"])

    logical_ids = {logical_id for logical_id, _ in manifest}
    DatasetFile.objects.filter(dataset=dataset).exclude(id__in=logical_ids).delete()

    for logical_id, stored_object in manifest:
        DatasetFile.objects.update_or_create(
            id=logical_id,
            defaults={
                "dataset": dataset,
                "stored_object": stored_object,
                "file_key": stored_object.storage_key,
                "original_filename": stored_object.original_filename,
                "file_type": stored_object.file_type,
                "file_size": stored_object.file_size,
                "checksum": stored_object.checksum_sha256,
                "is_structured": stored_object.is_structured,
                "column_count": stored_object.column_count,
                "feature_names": stored_object.feature_names,
                "item_count": stored_object.item_count,
            },
        )


def _set_published_pointer(dataset, release):
    dataset.published_release = release
    dataset.save(update_fields=["published_release", "updated_at"])


def _create_proposal_submitter_attribution(proposal, release):
    """Record the proposal submitter as a contributor to this published release.

    Attribution is created only inside the successful publication transaction.
    Names and affiliations are copied now so the release record remains stable if
    the user's profile changes later. A deleted submitter (SET_NULL) cannot be
    attributed automatically and is intentionally skipped rather than inventing
    a public name.
    """
    submitter = proposal.submitted_by
    if submitter is None:
        return None

    from apps.accounts.models import UserProfile

    profile = UserProfile.objects.select_for_update().filter(user_id=submitter.pk).first()
    contributor = (
        proposal.dataset.contributors.filter(user_id=submitter.pk)
        .order_by("order", "id")
        .first()
    )
    name = (
        (contributor.name or "").strip()
        if contributor and (contributor.name or "").strip()
        else (getattr(profile, "full_name", "") or "").strip()
    )
    if not name:
        name = (submitter.get_full_name() or "").strip()
    if not name:
        raise PublicationIntegrityError(
            "Cannot attribute the published proposal: the submitter has no public display name."
        )

    affiliation = (getattr(profile, "affiliation", "") or "").strip()
    attribution, _ = ContributorAttribution.objects.get_or_create(
        dataset=proposal.dataset,
        release=release,
        user=submitter,
        defaults={"name": name, "affiliation": affiliation},
    )
    return attribution


def _legacy_manifest_matches_release(dataset, release):
    """Return whether the live V1 file rows still represent this release exactly."""
    from apps.datasets.models import DatasetFile

    release_manifest = sorted(
        (
            str(entry.logical_file_id),
            entry.stored_object.storage_key,
            str(entry.stored_object_id),
        )
        for entry in release.files.select_related("stored_object")
    )
    legacy_manifest = sorted(
        (
            str(entry.id),
            entry.file_key,
            str(entry.stored_object_id) if entry.stored_object_id else "",
        )
        for entry in DatasetFile.objects.select_for_update().filter(dataset=dataset)
    )
    return bool(release_manifest) and legacy_manifest == release_manifest


# ---------------------------------------------------------------------------
# Proposed-metadata materialization
#
# proposal.proposed_metadata is a FLAT dict of changes:
#   - key absent             -> unchanged
#   - key present, value ""  -> intentionally cleared
#   - key present, value None-> cleared (text/list) or "unknown" (bool fields)
# Supported keys are listed below. Anything else is rejected, never ignored.
# ---------------------------------------------------------------------------

_FIELD_KINDS = {
    "title": "required_text",
    "description": "text",
    "keywords": "text_list",
    "languages": "text_list",
    "characteristics": "text_list",
    "coverage.geographic": "text",
    "coverage.temporal": "text",
    "methodology.collection_method": "text",
    "methodology.data_preprocessing": "text",
    "methodology.instances_represent": "text",
    "methodology.recommended_data_splits": "text",
    "methodology.sensitive_data_disclosure": "text",
    "methodology.has_header": "bool_or_none",
    "methodology.has_missing_values": "bool_or_none",
    "citation.doi_citation": "text",
    "citation.citation_notes": "text",
    "citation.collaborators_text": "text",
    "citation.sponsor_or_grant": "text",
    "resources.related_resources": "text",
    "category": "category",
    "creator.name": "text",
    "creator.affiliation": "text",
    "contributors": "contributors",
}


def _coerce(key, kind, value):
    if kind in ("text", "required_text"):
        if value is None:
            value = ""
        if not isinstance(value, str):
            raise InvalidProposalMetadataError(f"'{key}' must be a string.")
        value = value.strip()
        if kind == "required_text" and not value:
            raise InvalidProposalMetadataError(f"'{key}' cannot be empty.")
        return value
    if kind == "text_list":
        if value is None:
            return []
        if not isinstance(value, (list, tuple)) or not all(isinstance(v, str) for v in value):
            raise InvalidProposalMetadataError(f"'{key}' must be a list of strings.")
        return sorted({v.strip() for v in value if v.strip()})
    if kind == "bool_or_none":
        if value is not None and not isinstance(value, bool):
            raise InvalidProposalMetadataError(f"'{key}' must be true, false, or null.")
        return value
    if kind == "category":
        if not isinstance(value, dict):
            raise InvalidProposalMetadataError("'category' must be an object.")
        allowed = {"id", "name", "description"}
        if (
            set(value) - allowed
            or not value.get("id")
            or not isinstance(value.get("name"), str)
            or not value["name"].strip()
            or not isinstance(value.get("description", ""), str)
        ):
            raise InvalidProposalMetadataError(
                "'category' requires id and name, with optional description."
            )
        return {
            "id": str(value["id"]),
            "name": str(value["name"]).strip(),
            "description": str(value.get("description") or "").strip(),
        }
    if kind == "contributors":
        if not isinstance(value, list):
            raise InvalidProposalMetadataError("'contributors' must be a list.")
        cleaned = []
        for index, item in enumerate(value):
            if not isinstance(item, dict):
                raise InvalidProposalMetadataError(f"Contributor {index} must be an object.")
            allowed = {"name", "contributor_type", "order", "affiliation"}
            if set(item) - allowed or not isinstance(item.get("name"), str) or not item["name"].strip():
                raise InvalidProposalMetadataError(
                    f"Contributor {index} requires a name and only public attribution fields."
                )
            affiliation = item.get("affiliation", "")
            if not isinstance(affiliation, str):
                raise InvalidProposalMetadataError(f"Contributor {index} affiliation must be a string.")
            contributor_type = item.get("contributor_type", "contributor")
            if contributor_type not in {"owner", "co_author", "contributor"}:
                raise InvalidProposalMetadataError(f"Contributor {index} has an invalid type.")
            order = item.get("order", index + 1)
            if isinstance(order, bool) or not isinstance(order, int) or order < 1:
                raise InvalidProposalMetadataError(f"Contributor {index} has an invalid order.")
            cleaned.append({
                "name": item["name"].strip(),
                "contributor_type": contributor_type,
                "order": order,
                "affiliation": affiliation.strip(),
            })
        return sorted(cleaned, key=lambda item: (item["order"], item["name"].casefold()))
    raise InvalidProposalMetadataError(f"Unsupported field kind for '{key}'.")


def materialize_proposed_metadata(base_snapshot, proposed_metadata):
    """Return a NEW metadata snapshot = base snapshot + approved changes.
    Pure function: touches no database rows."""
    if not base_snapshot:
        raise PublicationIntegrityError("Base release has an empty metadata snapshot.")
    if not isinstance(base_snapshot, dict):
        raise PublicationIntegrityError("Base release metadata snapshot must be an object.")
    if base_snapshot.get("schema_version") != CURRENT_SNAPSHOT_SCHEMA_VERSION:
        raise PublicationIntegrityError(
            "Base release metadata snapshot uses an unsupported schema version."
        )

    if proposed_metadata is None:
        proposed_metadata = {}
    if not isinstance(proposed_metadata, dict):
        raise InvalidProposalMetadataError(
            f"proposed_metadata must be a dictionary, got {type(proposed_metadata).__name__}."
        )

    snapshot = copy.deepcopy(base_snapshot)
    for key, value in proposed_metadata.items():
        kind = _FIELD_KINDS.get(key)
        if kind is None:
            raise InvalidProposalMetadataError(
                f"'{key}' is not a supported proposed-metadata field."
            )
        clean = _coerce(key, kind, value)
        *parents, leaf = key.split(".")
        target = snapshot
        for part in parents:
            target = target.get(part)
            if not isinstance(target, dict):
                raise PublicationIntegrityError(f"Base snapshot is missing section '{part}'.")
        target[leaf] = clean

    json.dumps(snapshot)  # must stay JSON-serializable
    return snapshot


def _validate_release_snapshot(release):
    if release.snapshot_schema_version != CURRENT_SNAPSHOT_SCHEMA_VERSION:
        raise PublicationIntegrityError(
            f"Release {release.version} uses unsupported snapshot schema "
            f"{release.snapshot_schema_version}."
        )
    if not isinstance(release.metadata_snapshot, dict):
        raise PublicationIntegrityError("Release metadata snapshot must be an object.")
    if release.metadata_snapshot.get("schema_version") != release.snapshot_schema_version:
        raise PublicationIntegrityError(
            "Release snapshot schema version does not match its metadata snapshot."
        )
    try:
        json.dumps(release.metadata_snapshot)
    except (TypeError, ValueError) as exc:
        raise PublicationIntegrityError("Release metadata snapshot is not JSON-serializable.") from exc


# ---------------------------------------------------------------------------
# Initial publication
# ---------------------------------------------------------------------------

def publish_initial_dataset(dataset, published_by, license_data=None, require_verified=True):
    """Create release 1.0.0 for a dataset that has passed review.

    Safe to retry: if the initial release already exists it is returned.
    """
    with transaction.atomic():
        locked = Dataset.objects.select_for_update().get(pk=dataset.pk)

        if locked.published_release_id:
            existing = locked.published_release
            if (
                existing.base_release_id is None
                and existing.version == "1.0.0"
                and existing.status == DatasetRelease.Status.PUBLISHED
            ):
                ensure_release_identifiers(existing)
                return existing
            raise PublicationIntegrityError(
                "Dataset already has a published release; use publish_proposal()."
            )
        if locked.releases.exists():
            initial_rel = locked.releases.filter(base_release__isnull=True, major=1, minor=0, patch=0).first()
            if initial_rel:
                if initial_rel.status == DatasetRelease.Status.WITHDRAWN:
                    raise DatasetNotPublishableError("Initial release 1.0.0 has been withdrawn.")
                if initial_rel.status == DatasetRelease.Status.PUBLISHED:
                    _set_published_pointer(locked, initial_rel)
                    ensure_release_identifiers(initial_rel)
                    return initial_rel
            raise PublicationIntegrityError(
                "Dataset has releases but no published_release pointer."
            )
        if not locked.is_active:
            raise DatasetNotPublishableError("Dataset is not active.")
        if locked.status != Dataset.Status.PUBLISHED:
            raise DatasetNotPublishableError(
                f"Dataset has not passed review (status='{locked.status}')."
            )

        files = list(locked.files.select_related("stored_object").order_by("uploaded_at", "id"))
        if not files:
            raise EmptyDatasetError("Dataset has no files to publish.")

        manifest = []
        for dataset_file in files:
            label = f"File '{dataset_file.original_filename or dataset_file.id}'"
            _validate_stored_object(dataset_file.stored_object, locked, label, require_verified)
            if (
                dataset_file.file_key != dataset_file.stored_object.storage_key
                or dataset_file.file_size != dataset_file.stored_object.file_size
                or (dataset_file.checksum or "").strip().lower()
                != dataset_file.stored_object.checksum_sha256
            ):
                raise InvalidStoredObjectError(
                    f"{label}: legacy DatasetFile metadata does not match its StoredObject."
                )
            manifest.append((dataset_file.id, dataset_file.stored_object))  # logical id = DatasetFile.id
        _check_unique_objects(manifest)

        major, minor, patch = calculate_next_version(None, None)  # (1, 0, 0)

        metadata_row, locked_profiles = lock_metadata_snapshot_sources(locked)
        metadata_snapshot = generate_metadata_snapshot(
            locked,
            metadata_override=metadata_row,
            profiles=locked_profiles,
        )
        # Decision: citation snapshot is derived directly from the exact same metadata snapshot.
        citation_snapshot = citation_from_metadata_snapshot(metadata_snapshot)
        license_snapshot = generate_license_snapshot(license_data)

        release = DatasetRelease.objects.create(
            dataset=locked,
            major=major, minor=minor, patch=patch,
            status=DatasetRelease.Status.DRAFT,
            change_type=None,
            snapshot_schema_version=CURRENT_SNAPSHOT_SCHEMA_VERSION,
            metadata_snapshot=metadata_snapshot,
            citation_snapshot=citation_snapshot,
            license_snapshot=license_snapshot,
            base_release=None,
            created_by=published_by,
            published_by=published_by,
            published_at=timezone.now(),  # real publication moment
        )
        _create_release_files(release, manifest)
        release.status = DatasetRelease.Status.PUBLISHED
        release.save(update_fields=["status"])
        _set_published_pointer(locked, release)
        ensure_release_identifiers(release)
        return release


# ---------------------------------------------------------------------------
# Proposal creation & manifest manipulation
#
# Decision 1: A DatasetProposal represents a complete proposed file manifest.
# Decision 2: When a proposal is created, its manifest initially copies the base
#             release's complete manifest.
# Decision 3: The proposal can add, replace, and remove files.
# ---------------------------------------------------------------------------

_AUDITED_METADATA_DELTA_PATHS = {
    "description": ("description",),
    "sponsor_or_grant": ("citation", "sponsor_or_grant"),
    "related_resources": ("resources", "related_resources"),
    "has_header": ("methodology", "has_header"),
    "has_missing_values": ("methodology", "has_missing_values"),
    "recommended_data_splits": ("methodology", "recommended_data_splits"),
    "sensitive_data_disclosure": ("methodology", "sensitive_data_disclosure"),
    "citation_notes": ("citation", "citation_notes"),
}


def _audited_metadata_delta(base_snapshot, effective_snapshot):
    """Translate audited direct corrections into proposal materializer keys."""
    delta = {}
    for key, path in _AUDITED_METADATA_DELTA_PATHS.items():
        old = base_snapshot
        new = effective_snapshot
        for part in path:
            old = old.get(part) if isinstance(old, dict) else None
            new = new.get(part) if isinstance(new, dict) else None
        if old != new:
            delta[key] = copy.deepcopy(new)
    return delta


def create_dataset_proposal(
    *,
    dataset: Dataset,
    base_release: DatasetRelease,
    submitted_by,
    source: str = DatasetProposal.Source.OWNER_EDIT,
    proposed_metadata: Optional[Dict[str, Any]] = None,
    proposed_license: Optional[Dict[str, Any]] = None,
    license_change_reason: str = "",
    declared_change_type: Optional[str] = None,
    change_summary: str = "",
    release_notes: str = "",
) -> DatasetProposal:
    """Create a new proposal based on base_release.

    Initializes its proposed file manifest by copying all entries from base_release.
    """
    with transaction.atomic():
        locked_dataset = Dataset.objects.select_for_update().get(pk=dataset.pk)
        locked_base = DatasetRelease.objects.select_for_update().get(pk=base_release.pk)

        if locked_base.dataset_id != locked_dataset.id:
            raise PublicationIntegrityError("Base release belongs to a different dataset.")
        if locked_base.status == DatasetRelease.Status.WITHDRAWN:
            raise PublicationIntegrityError("Cannot create a proposal from a withdrawn base release.")
        if locked_base.status != DatasetRelease.Status.PUBLISHED:
            raise PublicationIntegrityError("A proposal must be based on a published release.")
        if locked_dataset.published_release_id != locked_base.id:
            raise StaleProposalError("Base release is not the currently published release.")
        _validate_release_snapshot(locked_base)
        if not _legacy_manifest_matches_release(locked_dataset, locked_base):
            raise V1ContentConflictError(
                "The live V1 files differ from the current release. Reconcile the V1 changes "
                "before creating a V2 proposal."
            )

        existing_active = locked_dataset.proposals.filter(
            status__in=DatasetProposal.ACTIVE_STATUSES
        ).first()
        if existing_active:
            raise PublicationIntegrityError(
                f"Dataset already has an active proposal ({existing_active.id}, status='{existing_active.status}')."
            )

        if proposed_license:
            generate_license_snapshot(proposed_license)

        effective_snapshot = current_published_metadata_snapshot(locked_dataset)
        proposal_metadata = _audited_metadata_delta(
            locked_base.metadata_snapshot, effective_snapshot
        )
        proposal_metadata.update(proposed_metadata or {})

        proposal = DatasetProposal.objects.create(
            dataset=locked_dataset,
            base_release=locked_base,
            source=source,
            status=DatasetProposal.Status.DRAFT,
            proposed_metadata=proposal_metadata,
            proposed_license=proposed_license or {},
            license_change_reason=license_change_reason or "",
            declared_change_type=declared_change_type,
            change_summary=change_summary or "",
            release_notes=release_notes or "",
            submitted_by=submitted_by,
        )

        # Decision 2: Copy base release's complete manifest into DatasetProposalFile
        base_files = locked_base.files.select_related("stored_object").order_by("display_order")
        DatasetProposalFile.objects.bulk_create([
            DatasetProposalFile(
                proposal=proposal,
                stored_object=bf.stored_object,
                logical_file_id=bf.logical_file_id,
                display_order=bf.display_order,
            )
            for bf in base_files
        ])

        return proposal


def _lock_draft_proposal(proposal):
    locked = DatasetProposal.objects.select_for_update().get(pk=proposal.pk)
    if locked.status != DatasetProposal.Status.DRAFT:
        raise PublicationIntegrityError("Only draft proposals can be edited.")
    return locked


def add_proposal_file(proposal: DatasetProposal, stored_object, logical_file_id=None, display_order=None):
    """Add a new file to a proposal's manifest (Decision 3)."""
    with transaction.atomic():
        locked = _lock_draft_proposal(proposal)
        if stored_object.dataset_id != locked.dataset_id:
            raise PublicationIntegrityError("StoredObject belongs to a different dataset.")

        if logical_file_id is None:
            logical_file_id = uuid.uuid4()

        if locked.files.filter(logical_file_id=logical_file_id).exists():
            raise PublicationIntegrityError(
                f"File with logical_file_id {logical_file_id} already exists in proposal."
            )

        if locked.files.filter(stored_object=stored_object).exists():
            raise PublicationIntegrityError(
                f"StoredObject {stored_object.id} already exists in proposal manifest."
            )

        if display_order is None:
            max_order = locked.files.aggregate(models.Max("display_order"))["display_order__max"]
            display_order = (max_order + 1) if max_order is not None else 0

        return DatasetProposalFile.objects.create(
            proposal=locked,
            stored_object=stored_object,
            logical_file_id=logical_file_id,
            display_order=display_order,
        )


def replace_proposal_file(proposal: DatasetProposal, logical_file_id, new_stored_object):
    """Replace an existing file in the proposal's manifest with a new StoredObject (Decision 3)."""
    with transaction.atomic():
        locked = _lock_draft_proposal(proposal)
        if new_stored_object.dataset_id != locked.dataset_id:
            raise PublicationIntegrityError("StoredObject belongs to a different dataset.")

        try:
            entry = locked.files.get(logical_file_id=logical_file_id)
        except DatasetProposalFile.DoesNotExist:
            raise PublicationIntegrityError(f"Logical file {logical_file_id} not found in proposal.")

        other = locked.files.filter(stored_object=new_stored_object).exclude(
            logical_file_id=logical_file_id
        ).first()
        if other:
            raise PublicationIntegrityError(
                f"StoredObject {new_stored_object.id} is already in the proposal manifest."
            )

        entry.stored_object = new_stored_object
        entry.save(update_fields=["stored_object"])
        return entry


def remove_proposal_file(proposal: DatasetProposal, logical_file_id):
    """Remove a file from the proposal's manifest (Decision 3)."""
    with transaction.atomic():
        locked = _lock_draft_proposal(proposal)
        deleted_count, _ = locked.files.filter(logical_file_id=logical_file_id).delete()
        if not deleted_count:
            raise PublicationIntegrityError(f"Logical file {logical_file_id} not found in proposal.")
        return True


# ---------------------------------------------------------------------------
# Proposal publication
# ---------------------------------------------------------------------------

def _build_manifest_from_proposal(proposal, dataset, require_verified):
    """A DatasetProposal represents a complete proposed file manifest (Decision 1 & 4).

    An empty final manifest is invalid (Decision 5).
    """
    entries = list(proposal.files.select_related("stored_object").order_by("display_order", "id"))
    if not entries:
        raise EmptyDatasetError("Proposal manifest is empty; an empty release manifest is invalid.")

    manifest = []
    for entry in entries:
        _validate_stored_object(entry.stored_object, dataset, "Proposal file", require_verified)
        manifest.append((entry.logical_file_id, entry.stored_object))

    _check_unique_objects(manifest)
    return manifest


def publish_proposal(proposal, published_by, final_change_type, require_verified=True):
    """Publish an approved proposal as the next release. Safe to retry."""
    dataset_id = DatasetProposal.objects.values_list("dataset_id", flat=True).get(pk=proposal.pk)
    stale = False
    content_conflict = False

    with transaction.atomic():
        # Lock order: dataset, then proposal.
        dataset = Dataset.objects.select_for_update().get(pk=dataset_id)
        locked = (
            DatasetProposal.objects.select_for_update(of=("self",))
            .select_related("base_release", "resulting_release")
            .get(pk=proposal.pk)
        )

        # Idempotency: already published -> return the same release if consistent.
        if locked.status == DatasetProposal.Status.PUBLISHED or locked.resulting_release_id:
            release = locked.resulting_release
            if (
                locked.status == DatasetProposal.Status.PUBLISHED
                and release is not None
                and release.dataset_id == dataset.id
            ):
                ensure_release_identifiers(release)
                return release
            raise PublicationIntegrityError(
                "Proposal has an inconsistent resulting-release relationship."
            )

        if locked.status == DatasetProposal.Status.STALE:
            raise StaleProposalError("Proposal is stale and cannot be published.")

        if locked.status != DatasetProposal.Status.APPROVED:
            raise ProposalNotApprovedError(
                f"Proposal status is '{locked.status}', expected 'approved'."
            )

        base_release = locked.base_release
        if base_release.dataset_id != dataset.id:
            raise PublicationIntegrityError("Proposal base release belongs to another dataset.")

        if base_release.status == DatasetRelease.Status.WITHDRAWN:
            stale_at = timezone.now()
            DatasetProposal.objects.transition(locked.pk, DatasetProposal.Status.STALE, last_activity_at=stale_at)
            DatasetProposalActivity.objects.create(
                proposal=locked, actor=published_by, action=DatasetProposalActivity.Action.STALE,
                from_status=DatasetProposal.Status.APPROVED, to_status=DatasetProposal.Status.STALE,
                reason="The proposal base release is no longer current.",
            )
            stale = True
        elif dataset.published_release_id != locked.base_release_id:
            stale_at = timezone.now()
            DatasetProposal.objects.transition(locked.pk, DatasetProposal.Status.STALE, last_activity_at=stale_at)
            DatasetProposalActivity.objects.create(
                proposal=locked, actor=published_by, action=DatasetProposalActivity.Action.STALE,
                from_status=DatasetProposal.Status.APPROVED, to_status=DatasetProposal.Status.STALE,
                reason="A newer release superseded the proposal base.",
            )
            stale = True
        elif not _legacy_manifest_matches_release(dataset, base_release):
            stale_at = timezone.now()
            DatasetProposal.objects.transition(locked.pk, DatasetProposal.Status.STALE, last_activity_at=stale_at)
            DatasetProposalActivity.objects.create(
                proposal=locked, actor=published_by, action=DatasetProposalActivity.Action.STALE,
                from_status=DatasetProposal.Status.APPROVED, to_status=DatasetProposal.Status.STALE,
                reason="The live V1 manifest conflicts with the proposal base release.",
            )
            content_conflict = True
        else:
            _validate_release_snapshot(base_release)
            if locked.computed_min_change_type is None:
                raise PublicationIntegrityError("Proposal has no computed_min_change_type.")
            declared = locked.declared_change_type or locked.computed_min_change_type
            change_type = validate_change_type_authority(
                declared, locked.computed_min_change_type, reviewer=final_change_type
            )
            latest_historical_release = dataset.releases.filter(
                status__in=[
                    DatasetRelease.Status.PUBLISHED,
                    DatasetRelease.Status.WITHDRAWN,
                ]
            ).order_by("-major", "-minor", "-patch").first()
            major, minor, patch = calculate_next_version(
                (
                    latest_historical_release.major,
                    latest_historical_release.minor,
                    latest_historical_release.patch,
                ),
                change_type,
            )

            metadata_snapshot = materialize_proposed_metadata(
                base_release.metadata_snapshot, locked.proposed_metadata
            )

            # Strict license change validation
            if locked.proposed_license:
                license_snapshot = generate_license_snapshot(locked.proposed_license)
                if license_snapshot != base_release.license_snapshot:
                    if not (locked.license_change_reason and locked.license_change_reason.strip()):
                        raise InvalidProposalMetadataError(
                            "A license change requires a non-empty license_change_reason."
                        )
            else:
                license_snapshot = copy.deepcopy(base_release.license_snapshot)

            manifest = _build_manifest_from_proposal(locked, dataset, require_verified)

            now = timezone.now()
            release = DatasetRelease.objects.create(
                dataset=dataset,
                major=major, minor=minor, patch=patch,
                status=DatasetRelease.Status.DRAFT,
                change_type=change_type.value,
                change_summary=locked.change_summary,
                release_notes=locked.release_notes or locked.change_summary,
                snapshot_schema_version=CURRENT_SNAPSHOT_SCHEMA_VERSION,
                metadata_snapshot=metadata_snapshot,
                citation_snapshot=citation_from_metadata_snapshot(metadata_snapshot),
                license_snapshot=license_snapshot,
                base_release=base_release,
                created_by=locked.submitted_by,
                published_by=published_by,
                published_at=now,
            )
            _create_release_files(release, manifest)
            release.status = DatasetRelease.Status.PUBLISHED
            release.save(update_fields=["status"])
            _create_proposal_submitter_attribution(locked, release)
            _sync_legacy_files(dataset, manifest)
            _set_published_pointer(dataset, release)

            DatasetProposal.objects.transition(
                locked.pk, DatasetProposal.Status.PUBLISHED,
                final_change_type=change_type.value, resulting_release_id=release.pk,
                published_at=now, last_activity_at=now,
            )
            ensure_release_identifiers(release)
            concept_pid = ResourceIdentifier.objects.filter(
                dataset=dataset, scheme=ResourceIdentifier.Scheme.ORDP,
                role=ResourceIdentifier.Role.CONCEPT,
            ).values_list("identifier_value", flat=True).first()
            version_pid = ResourceIdentifier.objects.filter(
                release=release, scheme=ResourceIdentifier.Scheme.ORDP,
                role=ResourceIdentifier.Role.VERSION,
            ).values_list("identifier_value", flat=True).first()
            DatasetProposalActivity.objects.create(
                proposal=locked, actor=published_by,
                action=DatasetProposalActivity.Action.PUBLISHED,
                from_status=DatasetProposal.Status.APPROVED,
                to_status=DatasetProposal.Status.PUBLISHED,
                details={
                    "release_id": str(release.pk),
                    "version": release.version,
                    "concept_pid": concept_pid,
                    "version_pid": version_pid,
                },
            )

            # Any other still-active proposal was built on the old release.
            stale_proposals = dataset.proposals.select_for_update().filter(
                status__in=DatasetProposal.ACTIVE_STATUSES
            ).exclude(pk=locked.pk).order_by("pk")
            for other_proposal in stale_proposals:
                DatasetProposal.objects.transition(
                    other_proposal.pk, DatasetProposal.Status.STALE, last_activity_at=now,
                )
                DatasetProposalActivity.objects.create(
                    proposal=other_proposal, actor=published_by,
                    action=DatasetProposalActivity.Action.STALE,
                    from_status=other_proposal.status, to_status=DatasetProposal.Status.STALE,
                    reason="A newer dataset release was published.",
                )

    if stale:
        raise StaleProposalError(
            "Proposal's base release is no longer current; it was marked stale and needs a rebase."
        )
    if content_conflict:
        raise V1ContentConflictError(
            "The live V1 files changed after the proposal was created. The proposal was marked "
            "stale; reconcile the V1 changes before creating a new proposal."
        )
    return release


# ---------------------------------------------------------------------------
# Withdrawn release handling
# ---------------------------------------------------------------------------

def withdraw_release(release: DatasetRelease, withdrawn_by, reason: str):
    """Withdraw a published release.

    - Sets status to WITHDRAWN, records withdrawn_at, withdrawn_by, withdrawal_reason.
    - If this release was dataset.published_release:
      Update dataset.published_release to the previous active release (or None).
    - Mark any active proposals based on this release as STALE.
    """
    if not (reason and reason.strip()):
        raise ValueError("A non-empty reason is required to withdraw a release.")

    with transaction.atomic():
        dataset = Dataset.objects.select_for_update().get(pk=release.dataset_id)
        locked_release = DatasetRelease.objects.select_for_update().get(pk=release.pk)
        if locked_release.status == DatasetRelease.Status.WITHDRAWN:
            raise ReleaseAlreadyWithdrawnError(f"Release {locked_release.version} is already withdrawn.")
        now = timezone.now()

        locked_release.status = DatasetRelease.Status.WITHDRAWN
        locked_release.withdrawn_at = now
        locked_release.withdrawn_by = withdrawn_by
        locked_release.withdrawal_reason = reason.strip()
        locked_release.save(update_fields=[
            "status", "withdrawn_at", "withdrawn_by", "withdrawal_reason",
        ])
        withdraw_release_identifiers(locked_release)

        # If this release was the current published_release:
        if dataset.published_release_id == locked_release.id:
            prev_release = dataset.releases.filter(
                status=DatasetRelease.Status.PUBLISHED
            ).order_by("-major", "-minor", "-patch").first()
            dataset.published_release = prev_release
            dataset.save(update_fields=["published_release", "updated_at"])
            if prev_release:
                _sync_legacy_files(
                    dataset,
                    [
                        (entry.logical_file_id, entry.stored_object)
                        for entry in prev_release.files.select_related(
                            "stored_object"
                        ).order_by("display_order", "id")
                    ],
                )

        # Any active proposal based on this release must become STALE
        proposals_to_stale = dataset.proposals.select_for_update().filter(
            base_release=locked_release,
            status__in=DatasetProposal.ACTIVE_STATUSES,
        ).order_by("pk")
        for proposal in proposals_to_stale:
            DatasetProposal.objects.transition(
                proposal.pk,
                DatasetProposal.Status.STALE,
                last_activity_at=now,
            )

        return locked_release
