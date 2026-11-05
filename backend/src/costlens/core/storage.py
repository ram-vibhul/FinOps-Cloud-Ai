"""File storage for generated files (exports, reports).

Local filesystem under ``<data_dir>/files`` for now; an S3 backend for the SaaS
arrives with the AWS hosting work (D.6). Keys are relative paths we generate,
never user input.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Protocol

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


def build_storage(data_dir: str) -> Storage:
    return LocalStorage(Path(data_dir) / "files")
