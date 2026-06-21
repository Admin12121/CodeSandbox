from __future__ import annotations

import uuid
from functools import lru_cache

import boto3
from botocore.client import Config

from codesandbox.config import get_settings

_ALLOWED_TYPES = {"image/png", "image/jpeg", "image/webp", "image/svg+xml"}
_EXTS = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp", "image/svg+xml": "svg"}
_MAX_BYTES = 2 * 1024 * 1024  # 2 MB


@lru_cache(maxsize=1)
def _s3():
    s = get_settings()
    return boto3.client(
        "s3",
        endpoint_url=s.s3_endpoint,
        aws_access_key_id=s.s3_access_key,
        aws_secret_access_key=s.s3_secret_key,
        config=Config(signature_version="s3v4"),
        region_name="us-east-1",
    )


def _ensure_bucket(bucket: str) -> None:
    client = _s3()
    try:
        client.head_bucket(Bucket=bucket)
    except Exception:
        client.create_bucket(Bucket=bucket)
        # Make bucket publicly readable so image URLs work without auth
        client.put_bucket_policy(
            Bucket=bucket,
            Policy=f'{{"Version":"2012-10-17","Statement":[{{"Effect":"Allow","Principal":"*","Action":"s3:GetObject","Resource":"arn:aws:s3:::{bucket}/*"}}]}}',
        )


def upload_image(data: bytes, mime: str, prefix: str = "uploads") -> str | None:
    """Upload image bytes to S3/MinIO. Returns the public URL or None on failure."""
    if mime not in _ALLOWED_TYPES:
        return None
    if len(data) > _MAX_BYTES:
        return None
    s = get_settings()
    bucket = s.bucket if hasattr(s, "bucket") else s.s3_bucket
    try:
        _ensure_bucket(bucket)
        ext = _EXTS[mime]
        key = f"{prefix}/{uuid.uuid4().hex}.{ext}"
        _s3().put_object(
            Bucket=bucket,
            Key=key,
            Body=data,
            ContentType=mime,
        )
        # Build public URL pointing at MinIO through the same host
        # In dev: MinIO runs at S3_ENDPOINT (http://minio:9000 inside Docker)
        # Nginx proxies /media/ → MinIO so external browsers can reach it
        return f"/media/{bucket}/{key}"
    except Exception:
        return None


def upload_image_from_filestorage(file_storage, prefix: str = "uploads") -> str | None:
    """Wrapper for Werkzeug FileStorage objects."""
    if not file_storage or not file_storage.filename:
        return None
    mime = file_storage.mimetype or ""
    data = file_storage.read()
    return upload_image(data, mime, prefix=prefix)
