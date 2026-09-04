from datetime import timedelta
from urllib.parse import urlparse

import boto3
from botocore.client import Config
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
    storage_client().upload_file(local_path, settings.OBJECT_STORAGE_BUCKET, object_key)
    return object_key


def upload_fileobj(fileobj, object_key, content_type=None):
    extra = {"ContentType": content_type} if content_type else None
    kwargs = {"ExtraArgs": extra} if extra else {}
    storage_client().upload_fileobj(fileobj, settings.OBJECT_STORAGE_BUCKET, object_key, **kwargs)
    return object_key


def presigned_download_url(object_key, expires_seconds=3600):
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

