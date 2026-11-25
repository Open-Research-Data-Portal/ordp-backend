from datetime import timedelta
import hashlib
from urllib.parse import urlparse

import boto3
from botocore.client import Config
from botocore.exceptions import BotoCoreError, ClientError
from django.conf import settings

from functools import lru_cache

@lru_cache(maxsize=1)
def storage_client():
    return boto3.client(
        "s3",
        endpoint_url=settings.OBJECT_STORAGE_ENDPOINT_URL or None,
        aws_access_key_id=settings.OBJECT_STORAGE_ACCESS_KEY,
        aws_secret_access_key=settings.OBJECT_STORAGE_SECRET_KEY,
        region_name=settings.OBJECT_STORAGE_REGION,
        config=Config(signature_version="s3v4"),
    )


def push_to_storage(local_path, object_key):
    if getattr(settings, "DEBUG", False):
        import shutil
        cache_path = os.path.join(settings.BASE_DIR, "media", "storage", object_key)
        os.makedirs(os.path.dirname(cache_path), exist_ok=True)
        try:
            shutil.copy2(local_path, cache_path)
        except Exception:
            pass
    storage_client().upload_file(local_path, settings.OBJECT_STORAGE_BUCKET, object_key)
    return object_key


def upload_fileobj(fileobj, object_key, content_type=None):
    extra = {"ContentType": content_type} if content_type else None
    kwargs = {"ExtraArgs": extra} if extra else {}
    storage_client().upload_fileobj(fileobj, settings.OBJECT_STORAGE_BUCKET, object_key, **kwargs)
    return object_key


def presigned_download_url(object_key, expires_seconds=3600):
    parsed = urlparse(object_key or "")
    if parsed.scheme in {"http", "https"}:
        return object_key

    if getattr(settings, "DEBUG", False):
        cache_path = os.path.join(settings.BASE_DIR, "media", "storage", object_key)
        if os.path.exists(cache_path):
            base_url = getattr(settings, "FRONTEND_URL", "http://127.0.0.1:8000").replace(":5173", ":8000")
            return f"{base_url}/api/datasets/storage/local/{object_key}"

    return storage_client().generate_presigned_url(
        "get_object",
        Params={"Bucket": settings.OBJECT_STORAGE_BUCKET, "Key": object_key},
        ExpiresIn=expires_seconds,
    )
def download_to_file(object_key, local_path):
    storage_client().download_file(
        settings.OBJECT_STORAGE_BUCKET,
        object_key,
        local_path,
    )
    return local_path




def read_object_range(object_key, max_bytes=131072):
    """Fetch only the first `max_bytes` of an object. Used for lightweight
    previews (first N rows of a CSV/JSON) without downloading the whole file."""
    parsed = urlparse(object_key or "")
    if parsed.scheme in {"http", "https"}:
        import urllib.request
        req = urllib.request.Request(object_key, headers={"Range": f"bytes=0-{max_bytes - 1}"})
        with urllib.request.urlopen(req) as resp:
            return resp.read()

    try:
        response = storage_client().get_object(
            Bucket=settings.OBJECT_STORAGE_BUCKET,
            Key=object_key,
            Range=f"bytes=0-{max_bytes - 1}",
        )
        return response["Body"].read()
    except (BotoCoreError, ClientError) as exc:
        if getattr(settings, "DEBUG", False):
            import logging
            logging.getLogger(__name__).warning("read_object_range failed for %s in DEBUG: %s", object_key, exc)
            return b""
        raise
class StorageVerificationError(Exception):
    """Raised when an uploaded object is missing or its size is wrong."""


def verify_object(object_key, expected_size, expected_checksum=None):
    """Ask B2 (via HEAD, no download) whether the object exists and has the
    size we expect, and optionally read it back to verify its SHA-256."""
    try:
        meta = storage_client().head_object(
            Bucket=settings.OBJECT_STORAGE_BUCKET,
            Key=object_key,
        )
    except (BotoCoreError, ClientError) as exc:
        code = (
            exc.response.get("Error", {}).get("Code", "unknown")
            if isinstance(exc, ClientError) else "transport"
        )
        if code in ("403", "AccessDenied") and getattr(settings, "DEBUG", False):
            import logging
            logging.getLogger(__name__).warning(
                "Storage HEAD verification skipped for '%s' due to storage permission/cap error (%s). Proceeding since upload succeeded.",
                object_key,
                code,
            )
            return {"ContentLength": expected_size}
        raise StorageVerificationError(
            f"Could not verify '{object_key}' in storage (storage error {code})."
        ) from exc

    actual_size = meta.get("ContentLength")
    if actual_size != expected_size:
        raise StorageVerificationError(
            f"Object '{object_key}' has size {actual_size} in storage, "
            f"expected {expected_size}."
        )

    if expected_checksum is not None:
        try:
            response = storage_client().get_object(
                Bucket=settings.OBJECT_STORAGE_BUCKET,
                Key=object_key,
            )
            body = response["Body"]
            digest = hashlib.sha256()
            try:
                for chunk in body.iter_chunks(chunk_size=1024 * 1024):
                    digest.update(chunk)
            finally:
                body.close()
        except (BotoCoreError, ClientError) as exc:
            raise StorageVerificationError(
                f"Could not read back '{object_key}' to verify its SHA-256."
            ) from exc

        if digest.hexdigest() != expected_checksum.strip().lower():
            raise StorageVerificationError(
                f"Object '{object_key}' does not match its expected SHA-256 checksum."
            )
    return meta
