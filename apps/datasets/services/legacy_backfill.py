
import hashlib
import re
from urllib.parse import urlparse

from botocore.exceptions import BotoCoreError, ClientError
from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.datasets.models import DatasetFile, StoredObject
from apps.datasets.services.storage import StorageVerificationError, storage_client


SHA256_HEX_PATTERN = re.compile(r"^[0-9a-fA-F]{64}$")


def _validate_storage_key(key):
    """
    Validate a legacy object-storage key.

    Legacy keys may contain directories, but must not be URLs, empty values,
    absolute paths, or paths containing traversal segments.
    """
    if not isinstance(key, str) or not key or not key.strip():
        raise StorageVerificationError("Legacy storage key is empty or invalid.")

    if key != key.strip():
        raise StorageVerificationError(
            "Legacy storage key contains leading or trailing whitespace."
        )

    if "\x00" in key:
        raise StorageVerificationError("Legacy storage key contains a null byte.")

    parsed = urlparse(key)
    if parsed.scheme or parsed.netloc:
        raise StorageVerificationError(
            "Legacy storage key must be an object key, not a URL."
        )

    if key.startswith("/") or "\\" in key:
        raise StorageVerificationError("Legacy storage key is an invalid path.")

    if any(part in {".", ".."} for part in key.split("/")):
        raise StorageVerificationError(
            "Legacy storage key contains an invalid path segment."
        )

    return key


def _read_and_hash_legacy_object(key):
    """
    Read the actual object from configured storage and calculate its SHA-256.

    Returns (digest_hex, byte_count).
    Always closes the response body.
    """
    response = storage_client().get_object(
        Bucket=settings.OBJECT_STORAGE_BUCKET,
        Key=key,
    )

    body = response["Body"]
    digest = hashlib.sha256()
    byte_count = 0

    try:
        for chunk in body.iter_chunks(chunk_size=1024 * 1024):
            if not chunk:
                continue

            digest.update(chunk)
            byte_count += len(chunk)
    finally:
        body.close()

    return digest.hexdigest(), byte_count


def backfill_legacy_stored_objects(dataset_id=None, dry_run=False):
    """
    Backfill legacy DatasetFile rows with immutable StoredObject records.

    The checksum is calculated from the actual storage bytes. A legacy
    checksum that is not a valid SHA-256 value is replaced with the
    calculated checksum. A valid but mismatching SHA-256 is rejected.

    Dry-run performs storage verification but does not create or link
    database records.
    """
    result = {
        "scanned_count": 0,
        "linked_count": 0,
        "would_link_count": 0,
        "skipped_count": 0,
        "v2_skipped_count": 0,
        "errors": [],
    }

    files = (
        DatasetFile.objects.filter(stored_object__isnull=True)
        .select_related("dataset")
        .order_by("id")
    )

    if dataset_id:
        files = files.filter(dataset_id=dataset_id)

    seen_keys = set()

    for legacy_file in files.iterator():
        result["scanned_count"] += 1

        # Never backfill legacy rows for datasets already using V2 releases.
        if legacy_file.dataset.published_release_id is not None:
            result["skipped_count"] += 1
            result["v2_skipped_count"] += 1
            continue

        try:
            storage_key = _validate_storage_key(legacy_file.file_key)
        except StorageVerificationError as exc:
            result["errors"].append(f"{legacy_file.pk}: {exc}")
            continue

        if storage_key in seen_keys:
            result["errors"].append(
                f"{legacy_file.pk}: duplicate legacy storage key "
                f"'{storage_key}'."
            )
            continue

        seen_keys.add(storage_key)

        # Never reuse an existing StoredObject key.
        if StoredObject.objects.filter(storage_key=storage_key).exists():
            result["errors"].append(
                f"{legacy_file.pk}: storage key '{storage_key}' is already "
                "registered to a StoredObject."
            )
            continue

        try:
            actual_checksum, actual_size = _read_and_hash_legacy_object(
                storage_key
            )
        except (BotoCoreError, ClientError, OSError, KeyError, StorageVerificationError) as exc:
            result["errors"].append(
                f"{legacy_file.pk}: Could not read legacy file "
                f"'{storage_key}' from storage: {exc}"
            )
            continue

        if actual_size != legacy_file.file_size:
            result["errors"].append(
                f"{legacy_file.pk}: {actual_size} bytes in storage, but "
                f"the legacy record expects {legacy_file.file_size} bytes."
            )
            continue

        legacy_checksum = legacy_file.checksum or ""

        if SHA256_HEX_PATTERN.fullmatch(legacy_checksum.strip()):
            if legacy_checksum.strip().lower() != actual_checksum:
                result["errors"].append(
                    f"{legacy_file.pk}: legacy SHA-256 checksum does not "
                    "match the actual bytes in storage."
                )
                continue

        if dry_run:
            result["would_link_count"] += 1
            continue

        try:
            with transaction.atomic():
                stored_object = StoredObject.objects.create(
                    dataset=legacy_file.dataset,
                    storage_key=storage_key,
                    original_filename=(
                        legacy_file.original_filename or ""
                    )[:255],
                    file_type=legacy_file.file_type,
                    file_size=actual_size,
                    checksum_sha256=actual_checksum,
                    verified_at=timezone.now(),
                    uploaded_by=None,
                    is_structured=legacy_file.is_structured,
                    column_count=legacy_file.column_count,
                    feature_names=legacy_file.feature_names or [],
                    item_count=legacy_file.item_count,
                )

                legacy_file.stored_object = stored_object
                legacy_file.checksum = actual_checksum
                legacy_file.save(
                    update_fields=["stored_object", "checksum"]
                )

        except IntegrityError as exc:
            result["errors"].append(
                f"{legacy_file.pk}: database uniqueness or link conflict "
                f"while registering '{storage_key}': {exc}"
            )
            continue

        result["linked_count"] += 1

    return result
