from __future__ import annotations

import threading
import uuid

import boto3
from botocore.client import Config

from codesandbox.config import get_settings

_ALLOWED_TYPES = {"image/png", "image/jpeg", "image/jpg", "image/webp", "image/svg+xml"}
_EXTS = {"image/png": "png", "image/jpeg": "jpg", "image/jpg": "jpg", "image/webp": "webp", "image/svg+xml": "svg"}
_MAX_BYTES = 2 * 1024 * 1024  # 2 MB

_s3_lock = threading.Lock()
_s3_client = None
_bucket_ready: set[str] = set()


def _s3():
    global _s3_client
    if _s3_client is None:
        with _s3_lock:
            if _s3_client is None:
                s = get_settings()
                _s3_client = boto3.client(
                    "s3",
                    endpoint_url=s.s3_endpoint,
                    aws_access_key_id=s.s3_access_key,
                    aws_secret_access_key=s.s3_secret_key,
                    config=Config(signature_version="s3v4"),
                    region_name="us-east-1",
                )
    return _s3_client


def _ensure_bucket(client, bucket: str) -> None:
    """Create bucket and set public-read policy on first use only."""
    if bucket in _bucket_ready:
        return
    with _s3_lock:
        if bucket in _bucket_ready:
            return
        try:
            client.head_bucket(Bucket=bucket)
        except Exception:
            client.create_bucket(Bucket=bucket)
        client.put_bucket_policy(
            Bucket=bucket,
            Policy=(
                '{"Version":"2012-10-17","Statement":[{"Effect":"Allow",'
                f'"Principal":"*","Action":"s3:GetObject","Resource":"arn:aws:s3:::{bucket}/*"'
                '}]}'
            ),
        )
        _bucket_ready.add(bucket)


def upload_image(data: bytes, mime: str, prefix: str = "uploads") -> str | None:
    """Upload image bytes to S3/MinIO. Returns the public URL or None on failure."""
    if mime not in _ALLOWED_TYPES:
        return None
    if len(data) > _MAX_BYTES:
        return None
    s = get_settings()
    bucket = s.s3_bucket
    try:
        client = _s3()
        _ensure_bucket(client, bucket)
        ext = _EXTS[mime]
        key = f"{prefix}/{uuid.uuid4().hex}.{ext}"
        client.put_object(Bucket=bucket, Key=key, Body=data, ContentType=mime)
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
