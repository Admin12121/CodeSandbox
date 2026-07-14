from __future__ import annotations

import hashlib
import io
import os
import posixpath
import tarfile
import uuid

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError


def safe_relative_name(value: str) -> str:
    normalized = posixpath.normpath("/" + str(value or "")).lstrip("/")
    if not normalized or normalized.startswith("../") or "\x00" in normalized:
        raise ValueError("Invalid object name.")
    return normalized


class ObjectStore:
    def __init__(self) -> None:
        self.bucket = os.environ.get("S3_ARTIFACT_BUCKET", "codesandbox-artifacts")
        self.client = boto3.client(
            "s3",
            endpoint_url=os.environ.get("S3_ENDPOINT", "http://minio:9000"),
            aws_access_key_id=os.environ.get("S3_ACCESS_KEY", "minioadmin"),
            aws_secret_access_key=os.environ.get("S3_SECRET_KEY", "minioadmin"),
            config=Config(
                signature_version="s3v4",
                connect_timeout=int(os.environ.get("S3_CONNECT_TIMEOUT_SECONDS", "5")),
                read_timeout=int(os.environ.get("S3_READ_TIMEOUT_SECONDS", "30")),
                retries={
                    "max_attempts": int(os.environ.get("S3_MAX_ATTEMPTS", "3")),
                    "mode": "standard",
                },
            ),
            region_name="us-east-1",
        )
        self._ensure_bucket()

    def _ensure_bucket(self) -> None:
        try:
            self.client.head_bucket(Bucket=self.bucket)
            return
        except ClientError as exc:
            status = int(exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode") or 0)
            code = str(exc.response.get("Error", {}).get("Code") or "")
            if status not in {404} and code not in {"404", "NoSuchBucket", "NotFound"}:
                raise

        try:
            self.client.create_bucket(Bucket=self.bucket)
        except ClientError as exc:
            code = str(exc.response.get("Error", {}).get("Code") or "")
            if code not in {"BucketAlreadyExists", "BucketAlreadyOwnedByYou"}:
                raise
            self.client.head_bucket(Bucket=self.bucket)

    def get_input(self, instance_id: str, storage_key: str, max_bytes: int) -> bytes:
        expected = f"sandboxes/{instance_id}/inputs/"
        if not storage_key.startswith(expected):
            raise ValueError("Input object is outside this instance prefix.")
        response = self.client.get_object(Bucket=self.bucket, Key=storage_key)
        content_length = int(response.get("ContentLength") or 0)
        if content_length <= 0 or content_length > max_bytes:
            response["Body"].close()
            raise ValueError("Input object exceeds its policy limit.")
        data = response["Body"].read(max_bytes + 1)
        response["Body"].close()
        if not data or len(data) > max_bytes:
            raise ValueError("Input object exceeds its policy limit.")
        return data

    def put_artifact(self, prefix: str, name: str, data: bytes) -> dict:
        safe_name = safe_relative_name(name)
        key = f"{prefix.strip('/')}/{uuid.uuid4().hex}-{safe_name.replace('/', '_')}"
        checksum = hashlib.sha256(data).hexdigest()
        self.client.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=data,
            ContentType="application/octet-stream",
            Metadata={"sha256": checksum},
        )
        return {
            "name": safe_name,
            "artifact_type": "file",
            "storage_key": key,
            "size_bytes": len(data),
            "checksum": checksum,
        }


def tar_bytes(
    name: str,
    data: bytes,
    mode: int = 0o600,
    *,
    uid: int = 0,
    gid: int = 0,
) -> bytes:
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w") as archive:
        info = tarfile.TarInfo(name=safe_relative_name(name))
        info.size = len(data)
        info.mode = mode
        info.uid = uid
        info.gid = gid
        archive.addfile(info, io.BytesIO(data))
    return output.getvalue()


def directory_tar_bytes(
    name: str,
    mode: int = 0o700,
    *,
    uid: int = 0,
    gid: int = 0,
) -> bytes:
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w") as archive:
        info = tarfile.TarInfo(name=safe_relative_name(name).rstrip("/") + "/")
        info.type = tarfile.DIRTYPE
        info.mode = mode
        info.uid = uid
        info.gid = gid
        archive.addfile(info)
    return output.getvalue()


def extract_single_file(chunks) -> bytes:
    raw = b"".join(chunks)
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:*") as archive:
        member = next((item for item in archive.getmembers() if item.isfile()), None)
        if member is None:
            raise FileNotFoundError("Archive did not contain a regular file.")
        source = archive.extractfile(member)
        if source is None:
            raise FileNotFoundError("Archive file could not be read.")
        return source.read()


class ArtifactCollector:
    def __init__(self, store: ObjectStore, max_bytes: int | None = None) -> None:
        self.store = store
        self.max_bytes = max_bytes or int(
            os.environ.get("SANDBOX_MAX_ARTIFACT_BYTES", str(500 * 1024 * 1024))
        )

    def collect(self, container, paths: list[str], prefix: str) -> list[dict]:
        artifacts: list[dict] = []
        total = 0
        seen: set[str] = set()
        for root in paths:
            result = container.exec_run(["find", root, "-type", "f", "-print0"])
            if result.exit_code != 0:
                continue
            for raw_path in result.output.split(b"\0"):
                if not raw_path:
                    continue
                path = raw_path.decode("utf-8", errors="surrogateescape")
                if path in seen:
                    continue
                seen.add(path)
                chunks, stat = container.get_archive(path)
                size = int(stat.get("size") or 0)
                if size < 0 or total + size > self.max_bytes:
                    continue
                data = extract_single_file(chunks)
                if total + len(data) > self.max_bytes:
                    continue
                total += len(data)
                artifacts.append(
                    self.store.put_artifact(prefix, path.lstrip("/"), data)
                )
        return artifacts
