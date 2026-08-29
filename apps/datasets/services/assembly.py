import os
import hashlib
import shutil

from django.conf import settings

from apps.notifications.services import notify
from apps.notifications.models import Notification

from ..models import Dataset, DatasetFile, UploadSession
from .storage import push_to_storage
from .file_validation import (
    validate_file_matches_declared_type,
    FileTypeMismatchError,
)


class UploadTooLargeError(Exception):
    pass


class MissingChunksError(Exception):
    def __init__(self, missing_indexes):
        self.missing_indexes = missing_indexes
        super().__init__(
            f"Missing chunks: {', '.join(map(str, missing_indexes))}"
        )


def session_dir(upload_session_id):
    return os.path.join(
        settings.UPLOAD_TMP_DIR,
        str(upload_session_id),
    )


def running_total(upload_session_id):
    d = session_dir(upload_session_id)

    if not os.path.isdir(d):
        return 0

    total = 0

    for filename in os.listdir(d):
        path = os.path.join(d, filename)

        # Only count actual chunk files.
        if filename.startswith("chunk_") and os.path.isfile(path):
            total += os.path.getsize(path)

    return total


def finalize_upload(
    dataset_id,
    upload_session_id,
    uploader,
    original_filename,
    declared_file_type,
    is_structured=True,
    column_count=None,
    feature_names=None,
    item_count=None,
):
    dataset = Dataset.objects.get(id=dataset_id)

    # ---------------------------------------------------------
    # 0. Get upload session
    # ---------------------------------------------------------

    try:
        session = UploadSession.objects.select_related(
            "dataset",
            "uploader",
        ).get(
            id=upload_session_id,
            dataset=dataset,
            uploader=uploader,
        )
    except UploadSession.DoesNotExist:
        raise FileNotFoundError(
            f"Upload session not found: {upload_session_id}"
        )

    d = session_dir(upload_session_id)

    if not os.path.isdir(d):
        raise FileNotFoundError(
            f"Upload session not found: {upload_session_id}"
        )

    # ---------------------------------------------------------
    # 1. Verify upload was prepared
    # ---------------------------------------------------------

    if session.total_chunks is None:
        raise FileNotFoundError(
            f"Total chunk count is not set for session: "
            f"{upload_session_id}"
        )

    # ---------------------------------------------------------
    # 2. Verify ALL expected chunks are present
    # ---------------------------------------------------------

    expected_indexes = set(range(session.total_chunks))

    received_indexes = set()

    for filename in os.listdir(d):
        if not filename.startswith("chunk_"):
            continue

        path = os.path.join(d, filename)

        if not os.path.isfile(path):
            continue

        try:
            index = int(filename[len("chunk_"):])
        except ValueError:
            continue

        received_indexes.add(index)

    missing_indexes = sorted(
        expected_indexes - received_indexes
    )

    if missing_indexes:
        raise MissingChunksError(missing_indexes)

    # ---------------------------------------------------------
    # 3. Build ordered chunk paths
    # ---------------------------------------------------------

    chunk_paths = [
        os.path.join(
            d,
            f"chunk_{index:06d}",
        )
        for index in range(session.total_chunks)
    ]

    # Make absolutely sure every expected file exists.
    missing_files = [
        index
        for index, path in enumerate(chunk_paths)
        if not os.path.isfile(path)
    ]

    if missing_files:
        raise MissingChunksError(missing_files)

    # ---------------------------------------------------------
    # 4. Calculate total size
    # ---------------------------------------------------------

    total_size = sum(
        os.path.getsize(path)
        for path in chunk_paths
    )

    # ---------------------------------------------------------
    # 5. Size validation
    # ---------------------------------------------------------

    if total_size > settings.MAX_DATASET_UPLOAD_SIZE:
        shutil.rmtree(d, ignore_errors=True)

        limit_gb = (
            settings.MAX_DATASET_UPLOAD_SIZE
            // (1024 ** 3)
        )

        notify(
            user=uploader,
            notification_type=Notification.NotificationType.UPLOAD_FAILURE,
            message=(
                f'Upload of "{original_filename}" rejected: '
                f'exceeds the {limit_gb}GB limit.'
            ),
            dataset=dataset,
        )

        raise UploadTooLargeError(total_size)

    # ---------------------------------------------------------
    # 6. Assemble chunks in order + calculate final SHA-256
    # ---------------------------------------------------------

    assembled_path = os.path.join(
        d,
        "_assembled",
    )

    sha256 = hashlib.sha256()

    with open(assembled_path, "wb") as out:
        for chunk_path in chunk_paths:
            with open(chunk_path, "rb") as chunk:
                while True:
                    data = chunk.read(1024 * 1024)

                    if not data:
                        break

                    sha256.update(data)
                    out.write(data)

    checksum = sha256.hexdigest()

    # ---------------------------------------------------------
    # 7. Verify complete-file checksum
    # ---------------------------------------------------------

    if session.file_checksum:
        expected_checksum = (
            session.file_checksum.strip().lower()
        )

        if checksum.lower() != expected_checksum:
            notify(
                user=uploader,
                notification_type=Notification.NotificationType.UPLOAD_FAILURE,
                message=(
                    f'Upload of "{original_filename}" failed '
                    f'final checksum verification.'
                ),
                dataset=dataset,
            )

            raise ValueError(
                "Final file checksum does not match "
                "the expected checksum."
            )

    # ---------------------------------------------------------
    # 8. Validate declared file type
    # ---------------------------------------------------------

    try:
        validate_file_matches_declared_type(
            assembled_path,
            declared_file_type,
        )

    except FileTypeMismatchError as exc:
        notify(
            user=uploader,
            notification_type=Notification.NotificationType.UPLOAD_FAILURE,
            message=(
                f'Upload of "{original_filename}" rejected: {exc}'
            ),
            dataset=dataset,
        )

        # Keep the session so the user can retry.
        raise

    # ---------------------------------------------------------
    # 9. Push assembled file to storage
    # ---------------------------------------------------------

    object_key = (
        f"datasets/{dataset.id}/{original_filename}"
    )

    try:
        push_to_storage(
            assembled_path,
            object_key,
        )

    except Exception as exc:
        notify(
            user=uploader,
            notification_type=Notification.NotificationType.UPLOAD_FAILURE,
            message=(
                f'Upload of "{original_filename}" '
                f'failed during storage: {exc}'
            ),
            dataset=dataset,
        )

        # Keep session for retry.
        raise

    # ---------------------------------------------------------
    # 10. Create DatasetFile
    # ---------------------------------------------------------

    dataset_file = DatasetFile.objects.create(
        dataset=dataset,
        file_key=object_key,
        original_filename=original_filename,
        file_type=declared_file_type,
        file_size=total_size,
        checksum=checksum,
        is_structured=is_structured,
        column_count=column_count,
        feature_names=feature_names or [],
        item_count=item_count,
    )

    # ---------------------------------------------------------
    # 11. Success -> delete temporary upload session
    # ---------------------------------------------------------

    shutil.rmtree(
        d,
        ignore_errors=True,
    )

    notify(
        user=uploader,
        notification_type=Notification.NotificationType.UPLOAD_SUCCESS,
        message=(
            f'"{original_filename}" uploaded successfully '
            f'to "{dataset.title}".'
        ),
        dataset=dataset,
        link_path=f"/datasets/{dataset.id}",
    )

    return dataset_file
