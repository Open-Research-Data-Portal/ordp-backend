from django.conf import settings
from minio import Minio

minio_client = Minio(
    settings.MINIO_ENDPOINT,
    access_key=settings.MINIO_ACCESS_KEY,
    secret_key=settings.MINIO_SECRET_KEY,
    secure=settings.MINIO_SECURE,
)


def push_to_minio(local_path, object_key):
    minio_client.fput_object(settings.MINIO_BUCKET, object_key, local_path)
    return object_key


def presigned_download_url(object_key, expires_seconds=3600):
    from datetime import timedelta
    return minio_client.presigned_get_object(
        settings.MINIO_BUCKET, object_key, expires=timedelta(seconds=expires_seconds)
    )