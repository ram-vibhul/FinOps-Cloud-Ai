"""File storage for uploaded cost files and generated files (exports, reports).

Local filesystem under ``<data_dir>/files`` by default, or any S3-compatible object
store (AWS S3, MinIO, Cloudflare R2…) when ``CCRADAR_STORAGE_URL=s3://bucket/prefix``
is set (11.7): then web and worker need no shared volume. Keys are relative paths we
generate, never user input.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlsplit

_SAFE_KEY = re.compile(r"^[a-zA-Z0-9_\-/.]{1,300}$")


class Storage(Protocol):
    def put(self, key: str, data: bytes) -> None: ...
    def get(self, key: str) -> bytes: ...
    def delete(self, key: str) -> None: ...
    def exists(self, key: str) -> bool: ...


class LocalStorage:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def _path(self, key: str) -> Path:
        if not _SAFE_KEY.match(key) or ".." in key.split("/"):
            raise ValueError(f"unsafe storage key: {key!r}")
        path = (self.root / key).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError(f"unsafe storage key: {key!r}")
        return path

    def put(self, key: str, data: bytes) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_bytes(data)
        tmp.replace(path)

    def get(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()


class S3Storage:
    """Objects under ``s3://bucket/prefix``. Credentials come from the standard AWS chain
    (instance or pod role, environment); ``endpoint_url`` selects an S3-compatible service."""

    def __init__(self, url: str, *, client: Any = None, endpoint_url: str | None = None,
                 region: str | None = None) -> None:  # fmt: skip
        parsed = urlsplit(url)
        if parsed.scheme != "s3" or not parsed.netloc:
            raise ValueError("CCRADAR_STORAGE_URL must look like s3://bucket/optional/prefix")
        self.bucket = parsed.netloc
        self.prefix = parsed.path.strip("/")
        if client is None:
            import boto3

            client = boto3.client("s3", endpoint_url=endpoint_url, region_name=region)
        self.client = client

    def _key(self, key: str) -> str:
        if not _SAFE_KEY.match(key) or ".." in key.split("/"):
            raise ValueError(f"unsafe storage key: {key!r}")
        return f"{self.prefix}/{key}" if self.prefix else key

    def put(self, key: str, data: bytes) -> None:
        self.client.put_object(
            Bucket=self.bucket, Key=self._key(key), Body=data, ServerSideEncryption="AES256"
        )

    def get(self, key: str) -> bytes:
        return self.client.get_object(Bucket=self.bucket, Key=self._key(key))["Body"].read()

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=self._key(key))

    def exists(self, key: str) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket, Key=self._key(key))
        except Exception as exc:  # botocore ClientError: 404 means missing
            code = str((getattr(exc, "response", None) or {}).get("Error", {}).get("Code", ""))
            if code in ("404", "NoSuchKey", "NotFound"):
                return False
            raise
        return True


def build_storage(
    data_dir: str,
    url: str | None = None,
    endpoint_url: str | None = None,
    region: str | None = None,
) -> Storage:
    if url:
        return S3Storage(url, endpoint_url=endpoint_url, region=region)
    return LocalStorage(Path(data_dir) / "files")
