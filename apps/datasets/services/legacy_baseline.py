"""Create first immutable V2 releases for verified, already-published V1 datasets."""
from django.db import transaction

from apps.datasets.models import Dataset
from apps.datasets.services.file_integrity import verify_stored_object_in_storage
from apps.datasets.services.publication import (
    PublicationError,
    publish_initial_dataset,
)
from apps.datasets.services.storage import StorageVerificationError


def backfill_legacy_baseline_releases(*, dataset_id=None, dry_run=False):
    queryset = Dataset.objects.filter(status=Dataset.Status.PUBLISHED).order_by("id")
    if dataset_id:
        queryset = queryset.filter(pk=dataset_id)

    result = {
        "scanned_count": queryset.count(),
        "created_count": 0,
        "would_create_count": 0,
        "skipped_count": 0,
        "unverifiable_dataset_ids": [],
        "eligible_dataset_ids": [],
        "already_baselined_ids": [],
        "errors": [],
    }

    for dataset in queryset.iterator():
        if dataset.published_release_id:
            result["already_baselined_ids"].append(str(dataset.pk))
            result["skipped_count"] += 1
            continue
        try:
            locked_files = list(dataset.files.select_related("stored_object").order_by("uploaded_at", "id"))
            if not locked_files:
                raise StorageVerificationError("Dataset has no legacy files to baseline.")

            for legacy_file in locked_files:
                stored_object = legacy_file.stored_object
                if stored_object is None:
                    raise StorageVerificationError(
                        f"Legacy file {legacy_file.pk} has no verified StoredObject. "
                        "Run backfill_legacy_stored_objects first."
                    )
                if stored_object.dataset_id != dataset.pk:
                    raise StorageVerificationError(
                        f"StoredObject {stored_object.pk} belongs to another dataset."
                    )
                if (
                    legacy_file.file_key != stored_object.storage_key
                    or legacy_file.file_size != stored_object.file_size
                    or (legacy_file.checksum or "").strip().lower()
                    != stored_object.checksum_sha256
                ):
                    raise StorageVerificationError(
                        f"Legacy DatasetFile {legacy_file.pk} does not match its StoredObject metadata."
                    )
                verify_stored_object_in_storage(
                    stored_object, update_timestamp=not dry_run
                )

            if dry_run:
                result["would_create_count"] += 1
                result["eligible_dataset_ids"].append(str(dataset.pk))
                continue

            with transaction.atomic():
                current = Dataset.objects.select_for_update().get(pk=dataset.pk)
                if current.published_release_id:
                    result["skipped_count"] += 1
                    continue
                release = publish_initial_dataset(
                    current,
                    published_by=current.owner,
                    require_verified=True,
                )
                if release.version != "1.0.0":
                    raise PublicationError(
                        f"Expected V2 baseline 1.0.0, got {release.version}."
                    )
                result["created_count"] += 1
                result["eligible_dataset_ids"].append(str(dataset.pk))
        except (StorageVerificationError, PublicationError, OSError, ValueError) as exc:
            result["unverifiable_dataset_ids"].append(str(dataset.pk))
            result["errors"].append(f"{dataset.pk}: {exc}")

    return result
