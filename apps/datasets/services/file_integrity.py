import os
import re
import uuid

from django.utils import timezone

from ..models import StoredObject
from .storage import push_to_storage, verify_object, StorageVerificationError

SHA256_HEX_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def is_valid_sha256(checksum: str) -> bool:
    """Return True if checksum is a strict 64-character lowercase hex SHA-256 string."""
    if not isinstance(checksum, str):
        return False
    return bool(SHA256_HEX_PATTERN.match(checksum.strip().lower()))


def validate_sha256(checksum: str) -> str:
    """Validate and normalize a SHA-256 checksum string.

    Returns the normalized lowercase hex string.
    Raises ValueError on any invalid input or pattern mismatch.
    """
    if not isinstance(checksum, str):
        raise ValueError(
            f"Checksum must be a string, got {type(checksum).__name__}: {checksum!r}"
        )
    cleaned = checksum.strip().lower()
    if not SHA256_HEX_PATTERN.match(cleaned):
        raise ValueError(
            f"Invalid SHA-256 checksum: '{checksum}'. Must be exactly 64 hexadecimal characters."
        )
    return cleaned


def safe_filename(name):
    """Strip any directory part so a client can never influence the
    folder structure of the storage key."""
    cleaned = os.path.basename((name or "").replace("\\", "/")).strip()
    return cleaned or "file"


def upload_immutable_object(
    *, local_path, dataset_id, original_filename, expected_size, expected_checksum,
):
    """Upload one assembled file to a brand-new, unique storage key, then
    verify it in storage. Returns (object_id, storage_key).

    This is the ONLY place that decides a V2 storage key, so no code path
    can reuse a key and overwrite an old physical object."""
    actual_local_size = os.path.getsize(local_path)
    if actual_local_size != expected_size:
        raise StorageVerificationError(
            f"Assembled file size {actual_local_size} does not match "
            f"expected size {expected_size}."
        )
    normalized_checksum = validate_sha256(expected_checksum)

    object_id = uuid.uuid4()
    storage_key = StoredObject.build_storage_key(
        dataset_id, object_id, safe_filename(original_filename)
    )

    push_to_storage(local_path, storage_key)
    verify_object(storage_key, expected_size, normalized_checksum)
    return object_id, storage_key


def create_stored_object_record(
    *, object_id, storage_key, dataset, original_filename, file_type,
    file_size, checksum, uploaded_by, is_structured=True,
    column_count=None, feature_names=None, item_count=None,
):
    """Create the DB row for an object that is already uploaded and verified."""
    if file_size is None or file_size < 0:
        raise ValueError(f"Invalid file_size: {file_size}. Must be non-negative.")

    normalized_checksum = validate_sha256(checksum)

    return StoredObject.objects.create(
        id=object_id,
        dataset=dataset,
        storage_key=storage_key,
        original_filename=(original_filename or "")[:255],
        file_type=file_type,
        file_size=file_size,
        checksum_sha256=normalized_checksum,
        verified_at=timezone.now(),
        uploaded_by=uploaded_by,
        is_structured=is_structured,
        column_count=column_count,
        feature_names=feature_names or [],
        item_count=item_count,
    )


def verify_stored_object_in_storage(stored_object, update_timestamp=True):
    """Verify that stored_object exists in storage and matches its recorded size.

    Sets verified_at timestamp on the StoredObject instance if update_timestamp is True.
    Raises StorageVerificationError if verification fails.
    """
    if stored_object.file_size is None or stored_object.file_size < 0:
        raise StorageVerificationError(
            f"StoredObject {stored_object.id} has invalid file_size: {stored_object.file_size}."
        )
    meta = verify_object(
        stored_object.storage_key,
        stored_object.file_size,
        validate_sha256(stored_object.checksum_sha256),
    )
    if update_timestamp:
        stored_object.verified_at = timezone.now()
        stored_object.save(update_fields=["verified_at"])
    return meta


def open_verified_object_for_download(stored_object):
    """Download once to a temporary file, verify size/SHA-256, then rewind.

    The returned file is caller-owned and should be closed by the HTTP response.
    Storage I/O is deliberately outside any publication database transaction.
    """
    import hashlib
    import tempfile
    from botocore.exceptions import BotoCoreError, ClientError
    from django.conf import settings
    from .storage import storage_client

    temp_file = tempfile.TemporaryFile(mode="w+b")
    try:
        storage_client().download_fileobj(
            settings.OBJECT_STORAGE_BUCKET,
            stored_object.storage_key,
            temp_file,
        )
        temp_file.flush()
        temp_file.seek(0, os.SEEK_END)
        size = temp_file.tell()
        if size != stored_object.file_size:
            raise StorageVerificationError(
                f"Stored object size {size} does not match expected size {stored_object.file_size}."
            )
        temp_file.seek(0)
        digest = hashlib.sha256()
        while True:
            chunk = temp_file.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
        if digest.hexdigest() != validate_sha256(stored_object.checksum_sha256):
            raise StorageVerificationError("Stored object checksum does not match its release manifest.")
        temp_file.seek(0)
        return temp_file
    except StorageVerificationError:
        temp_file.close()
        raise
    except (BotoCoreError, ClientError, OSError) as exc:
        temp_file.close()
        raise StorageVerificationError("Stored object could not be retrieved for verification.") from exc
    except Exception:
        temp_file.close()
        raise
