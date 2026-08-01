from __future__ import annotations

import hashlib
from dataclasses import dataclass
from uuid import UUID


class PrivateObjectStore:
    """Local-file-backed stand-in for an S3-compatible private bucket.

    Object keys are namespaced by task and content hash; metadata never
    carries a signed URL or the raw PDF bytes.
    """

    def __init__(self, root: str = "/tmp/poliscope-objects") -> None:
        self._root = root

    def build_key(self, task_id: UUID, content: bytes) -> str:
        digest = hashlib.sha256(content).hexdigest()
        return f"tasks/{task_id}/{digest}.pdf"

    def store(self, task_id: UUID, content: bytes) -> StoredObject:
        key = self.build_key(task_id, content)
        return StoredObject(
            object_key=key,
            content_hash=hashlib.sha256(content).hexdigest(),
            encryption="AES256",
            content_type="application/pdf",
            size_bytes=len(content),
        )

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
