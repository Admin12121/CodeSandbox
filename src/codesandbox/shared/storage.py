from __future__ import annotations

import logging
import threading
import uuid
import hashlib
from io import BytesIO

import boto3
from botocore.client import Config
from PIL import Image, ImageOps, UnidentifiedImageError
from werkzeug.utils import secure_filename

from codesandbox.config import get_settings

_logger = logging.getLogger(__name__)
_ALLOWED_TYPES = {"image/png", "image/jpeg", "image/jpg", "image/webp"}
_EXTS = {"image/png": "png", "image/jpeg": "jpg", "image/jpg": "jpg", "image/webp": "webp"}
_FORMAT_TO_MIME = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}
_MAX_BYTES = 2 * 1024 * 1024  # 2 MB
_MAX_PIXELS = 12_000_000

_s3_lock = threading.Lock()
_s3_client = None
_bucket_ready: set[tuple[str, bool]] = set()


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


def _ensure_bucket(client, bucket: str, *, public: bool = False) -> None:
    """Create a bucket once; public access is opt-in for image media only."""
    cache_key = (bucket, public)
    if cache_key in _bucket_ready:
        return
    with _s3_lock:
        if cache_key in _bucket_ready:
            return
        try:
            client.head_bucket(Bucket=bucket)
        except Exception:
            client.create_bucket(Bucket=bucket)
        if public:
            client.put_bucket_policy(
                Bucket=bucket,
                Policy=(
                    '{"Version":"2012-10-17","Statement":[{"Effect":"Allow",'
                    f'"Principal":"*","Action":"s3:GetObject","Resource":"arn:aws:s3:::{bucket}/*"'
                    '}]}'
                ),
            )
        _bucket_ready.add(cache_key)


def upload_image(data: bytes, mime: str, prefix: str = "uploads") -> str | None:
    """Upload image bytes to S3/MinIO. Returns the public URL or None on failure."""
    if mime not in _ALLOWED_TYPES:
        return None
    if not data or len(data) > _MAX_BYTES:
        return None
    s = get_settings()
    bucket = s.s3_bucket
    try:
        client = _s3()
        _ensure_bucket(client, bucket, public=True)
        ext = _EXTS[mime]
        key = f"{prefix}/{uuid.uuid4().hex}.{ext}"
        client.put_object(Bucket=bucket, Key=key, Body=data, ContentType=mime)
        return f"/media/{bucket}/{key}"
    except Exception as exc:
        _logger.error("Image upload to %s/%s failed: %s", bucket, prefix, exc)
        return None


def upload_image_from_filestorage(file_storage, prefix: str = "uploads") -> str | None:
    """Wrapper for Werkzeug FileStorage objects."""
    if not file_storage or not file_storage.filename:
        return None
    data = file_storage.read()
    validated = _validate_and_normalize_image(data)
    if not validated:
        return None
    safe_data, safe_mime = validated
    return upload_image(safe_data, safe_mime, prefix=prefix)


def upload_private_bytes(
    data: bytes,
    *,
    name: str,
    prefix: str,
    content_type: str = "application/octet-stream",
    bucket: str | None = None,
) -> dict | None:
    settings = get_settings()
    target_bucket = bucket or settings.s3_artifact_bucket
    safe_name = secure_filename(name or "file")[:180] or "file"
    if not data:
        return None
    key = f"{prefix.strip('/')}/{uuid.uuid4().hex}-{safe_name}"
    try:
        client = _s3()
        _ensure_bucket(client, target_bucket, public=False)
        client.put_object(
            Bucket=target_bucket,
            Key=key,
            Body=data,
            ContentType=content_type or "application/octet-stream",
        )
    except Exception as exc:
        _logger.error("Private upload to %s/%s failed: %s", target_bucket, prefix, exc)
        return None
    return {
        "bucket": target_bucket,
        "storage_key": key,
        "name": safe_name,
        "size_bytes": len(data),
        "checksum": hashlib.sha256(data).hexdigest(),
    }


def upload_private_filestorage(
    file_storage,
    *,
    prefix: str,
    max_bytes: int,
) -> dict | None:
    if not file_storage or not file_storage.filename:
        return None
    data = file_storage.stream.read(max_bytes + 1)
    if not data or len(data) > max_bytes:
        return None
    return upload_private_bytes(
        data,
        name=file_storage.filename,
        prefix=prefix,
        content_type=file_storage.mimetype or "application/octet-stream",
    )


def get_private_object(storage_key: str, *, bucket: str | None = None):
    settings = get_settings()
    target_bucket = bucket or settings.s3_artifact_bucket
    return _s3().get_object(Bucket=target_bucket, Key=storage_key)


def delete_private_object(storage_key: str, *, bucket: str | None = None) -> None:
    settings = get_settings()
    target_bucket = bucket or settings.s3_artifact_bucket
    _s3().delete_object(Bucket=target_bucket, Key=storage_key)


def _validate_and_normalize_image(data: bytes) -> tuple[bytes, str] | None:
    if not data or len(data) > _MAX_BYTES:
        return None

    previous_limit = Image.MAX_IMAGE_PIXELS
    Image.MAX_IMAGE_PIXELS = _MAX_PIXELS
    try:
        with Image.open(BytesIO(data)) as probe:
            fmt = (probe.format or "").upper()
            if fmt not in _FORMAT_TO_MIME:
                return None
            probe.verify()

        with Image.open(BytesIO(data)) as img:
            fmt = (img.format or "").upper()
            if fmt not in _FORMAT_TO_MIME:
                return None
            img = ImageOps.exif_transpose(img)
            out = BytesIO()
            if fmt == "JPEG":
                if img.mode not in ("RGB", "L"):
                    img = img.convert("RGB")
                img.save(out, format="JPEG", quality=88, optimize=True)
            elif fmt == "PNG":
                if img.mode not in ("RGB", "RGBA", "L", "LA", "P"):
                    img = img.convert("RGBA")
                img.save(out, format="PNG", optimize=True)
            else:
                if img.mode not in ("RGB", "RGBA"):
                    img = img.convert("RGBA")
                img.save(out, format="WEBP", quality=88, method=4)
            safe_data = out.getvalue()
    except (Image.DecompressionBombError, OSError, UnidentifiedImageError, ValueError):
        return None
    finally:
        Image.MAX_IMAGE_PIXELS = previous_limit

    if not safe_data or len(safe_data) > _MAX_BYTES:
        return None
    return safe_data, _FORMAT_TO_MIME[fmt]
