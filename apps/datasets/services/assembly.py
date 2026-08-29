import os
import hashlib
import shutil

from django.conf import settings

from apps.notifications.services import notify
from apps.notifications.models import Notification
from ..models import Dataset, DatasetFile
from .storage import push_to_storage
from .file_validation import (
    validate_file_matches_declared_type,
    FileTypeMismatchError,
)


class UploadTooLargeError(Exception):
    pass


def session_dir(upload_session_id):
    return os.path.join(settings.UPLOAD_TMP_DIR, upload_session_id)


def running_total(upload_session_id):
    d = session_dir(upload_session_id)
    if not os.path.isdir(d):
        return 0

    return sum(
        os.path.getsize(os.path.join(d, f))
        for f in os.listdir(d)
    )


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
    d = session_dir(upload_session_id)

    # Session must exist
    if not os.path.isdir(d):
        raise FileNotFoundError(
            f"Upload session not found: {upload_session_id}"
        )

    chunk_paths = sorted(
        os.path.join(d, f)
        for f in os.listdir(d)
        if f.startswith("chunk_")
    )

    if not chunk_paths:
        raise FileNotFoundError(
            f"No uploaded chunks found for session: {upload_session_id}"
        )

    total_size = sum(
        os.path.getsize(path)
        for path in chunk_paths
    )

    # ---------------------------------------------------------
    # 1. Size validation
    # ---------------------------------------------------------
    if total_size > settings.MAX_DATASET_UPLOAD_SIZE:
        shutil.rmtree(d, ignore_errors=True)

        limit_gb = settings.MAX_DATASET_UPLOAD_SIZE // (1024 ** 3)

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
    # 2. Assemble chunks
    # ---------------------------------------------------------
    assembled_path = os.path.join(d, "_assembled")

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
    # 3. Validate declared file type
    # ---------------------------------------------------------
    try:
        validate_file_matches_declared_type(
            assembled_path,
            declared_file_type,
        )

    except FileTypeMismatchError as exc:
        # IMPORTANT:
        # Keep the upload session so the user can correct
        # the selected file type and retry.
        notify(
            user=uploader,
            notification_type=Notification.NotificationType.UPLOAD_FAILURE,
            message=(
                f'Upload of "{original_filename}" rejected: {exc}'
            ),
            dataset=dataset,
        )

        raise

    # ---------------------------------------------------------
    # 4. Push to Backblaze/storage
    # ---------------------------------------------------------
    object_key = f"datasets/{dataset.id}/{original_filename}"

    try:
        push_to_storage(
            assembled_path,
            object_key,
        )

    except Exception as exc:
        # IMPORTANT:
        # Keep the session so the user does NOT have to
        # upload the chunks again after a temporary
        # storage/network failure.
        notify(
            user=uploader,
            notification_type=Notification.NotificationType.UPLOAD_FAILURE,
            message=(
                f'Upload of "{original_filename}" '
                f'failed during storage: {exc}'
            ),
            dataset=dataset,
        )

        raise

    # ---------------------------------------------------------
    # 5. Create DatasetFile
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
    # 6. SUCCESS → now delete temporary session
    # ---------------------------------------------------------
    shutil.rmtree(d, ignore_errors=True)

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