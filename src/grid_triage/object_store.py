"""Evidence object storage behind an S3-shaped port.

In production on AWS this port is Amazon S3 (objects are the
incident's raw alarm payloads, under versioning and an object-lock
retention posture). Locally and in tests the same port is served by
:class:`LocalObjectStore`, a plain directory tree — keys, puts and
gets behave identically; nothing in the domain layer knows which
one it is talking to.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Protocol


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class ObjectStore(Protocol):
    def put_object(self, key: str, data: bytes) -> str:
        """Store ``data`` under ``key``; return its SHA-256 hex digest."""
        ...

    def get_object(self, key: str) -> bytes:
        ...


class LocalObjectStore:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        path = (self.root / key).resolve()
        if not str(path).startswith(str(self.root.resolve())):
            raise ValueError(f"object key escapes the store root: {key}")
        return path

    def put_object(self, key: str, data: bytes) -> str:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return sha256_hex(data)

    def get_object(self, key: str) -> bytes:
        return self._path(key).read_bytes()
