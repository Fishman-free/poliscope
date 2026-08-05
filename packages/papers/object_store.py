from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID


class ObjectNotFound(Exception):
    """Raised by retrieve() for an object_key this store never wrote.

    Distinct from a bare FileNotFoundError so callers do not need to know this
    store happens to be file-backed today.
    """


class PrivateObjectStore:
    """Local-file-backed stand-in for an S3-compatible private bucket.

    Object keys are namespaced by task and content hash; metadata never
    carries a signed URL or the raw PDF bytes (CLAUDE.md 16: uploaded material
    must not leak through logs or exports).
    """

    def __init__(self, root: str = "/tmp/poliscope-objects") -> None:
        self._root = Path(root)

    @classmethod
    def from_env(cls) -> PrivateObjectStore:
        return cls(
            os.environ.get("POLISCOPE_OBJECT_STORE_ROOT", "/tmp/poliscope-objects")
        )

    def build_key(self, task_id: UUID, content: bytes) -> str:
        digest = hashlib.sha256(content).hexdigest()
        return f"tasks/{task_id}/{digest}.pdf"

    def store(self, task_id: UUID, content: bytes) -> StoredObject:
        return self.store_named(f"tasks/{task_id}", content)

    def store_named(
        self,
        namespace: str,
        content: bytes,
        *,
        suffix: str = ".pdf",
        content_type: str = "application/pdf",
    ) -> StoredObject:
        """Store bytes under ``namespace/<sha256>{suffix}``.

        Namespaced per collection so unrelated uploads cannot collide on the
        same content hash: a knowledge base keeps its documents under
        ``knowledge/{kb_id}``, a task under ``tasks/{task_id}``. The suffix
        and content type default to PDF -- the task-upload path's format --
        and the knowledge-base ingest passes the resolved values through so
        a docx stays a .docx in both the key and the metadata row.
        """
        digest = hashlib.sha256(content).hexdigest()
        key = f"{namespace}/{digest}{suffix}"
        path = self._root / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return StoredObject(
            object_key=key,
            content_hash=digest,
            encryption="AES256",
            content_type=content_type,
            size_bytes=len(content),
        )

    def retrieve(self, object_key: str) -> bytes:
        try:
            return (self._root / object_key).read_bytes()
        except FileNotFoundError as exc:
            raise ObjectNotFound(object_key) from exc

    def public_dto(self, stored: StoredObject) -> dict[str, object]:
        return {
            "object_key": stored.object_key,
            "content_hash": stored.content_hash,
            "encryption": stored.encryption,
            "content_type": stored.content_type,
            "size_bytes": stored.size_bytes,
        }


@dataclass(frozen=True, slots=True)
class StoredObject:
    object_key: str
    content_hash: str
    encryption: str
    content_type: str
    size_bytes: int

    def model_dump_json(self) -> str:
        """Explicitly excludes any signed URL or raw PDF content."""
        import json

        return json.dumps({
            "object_key": self.object_key,
            "content_hash": self.content_hash,
            "encryption": self.encryption,
            "content_type": self.content_type,
            "size_bytes": self.size_bytes,
        })
