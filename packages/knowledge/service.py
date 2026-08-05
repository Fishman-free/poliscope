"""Ingestion side of a knowledge base: bytes in, parsed text stored.

The repository persists rows; this service owns the pipeline that fills them
-- size/magic validation, object-store write, multi-format text extraction
(PDF/TXT/MD/CSV/DOCX/PPTX/XLSX, see ``extractors``) -- so the API layer stays
a thin mapping from HTTP to domain operations. `knowledge` imports `papers`
(parser, object_store) one-way; `papers` never imports `knowledge`, keeping
the module boundary acyclic.
"""

from __future__ import annotations

import hashlib
from uuid import UUID

from packages.knowledge.extractors import (
    CONTENT_TYPES,
    InvalidDocument,
    extract_text,
    file_type,
)
from packages.knowledge.repository import KnowledgeRepository, StoredKnowledgeDocument
from packages.papers.object_store import PrivateObjectStore

MAX_UPLOAD_BYTES = 20 * 1024 * 1024


class KnowledgeService:
    """Ingest and validation for knowledge-base documents."""

    def __init__(
        self,
        repository: KnowledgeRepository,
        object_store: PrivateObjectStore,
    ) -> None:
        self._repository = repository
        self._object_store = object_store

    async def ingest_document(
        self,
        kb_id: UUID,
        filename: str,
        content: bytes,
        created_by: str,
    ) -> StoredKnowledgeDocument:
        """Validate, store, parse, and persist one uploaded document.

        Never raises on the caller's behalf beyond :class:`InvalidDocument`:
        an empty file, an unsupported format, an oversized file, or content
        that yields no text are all refused here with the reason attached, so
        the API can map them to 422s without guessing what happened
        (CLAUDE.md 7). Legacy binary Office files are refused with a message
        telling the researcher to resave them.
        """
        if not content:
            raise InvalidDocument("uploaded file is empty")
        if len(content) > MAX_UPLOAD_BYTES:
            raise InvalidDocument("uploaded file exceeds the 20 MB limit")
        blocks, page_count = extract_text(content, filename)

        suffix, content_type = file_type(content, filename)
        stored = self._object_store.store_named(
            f"knowledge/{kb_id}",
            content,
            suffix=suffix,
            content_type=content_type,
        )
        text_content = "\n\n".join(page.text for page in blocks)
        return await self._repository.add_document(
            kb_id,
            title=filename,
            object_key=stored.object_key,
            content_hash=stored.content_hash,
            content_type=stored.content_type,
            size_bytes=stored.size_bytes,
            page_count=page_count,
            text_content=text_content,
            created_by=created_by,
        )

    async def add_text_document(
        self,
        kb_id: UUID,
        title: str,
        content: str,
        created_by: str,
    ) -> StoredKnowledgeDocument:
        """Persist a researcher's pasted text as a knowledge document.

        The text never touches the object store -- it is already plain text,
        stored directly as ``text_content`` for search -- but ``object_key``
        still gets a content-addressed value so the UNIQUE constraint (and
        any code that keys on object keys) keeps working. The Level A
        pipeline reads ``content_type`` to know it must extract from
        ``text_content`` rather than from PDF bytes (CLAUDE.md 7: a document
        that has no PDF form must never be parsed as one).
        """
        if not content.strip():
            raise InvalidDocument("text document must not be empty")
        if len(content.encode("utf-8")) > MAX_UPLOAD_BYTES:
            raise InvalidDocument("text document exceeds the 20 MB limit")
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        return await self._repository.add_document(
            kb_id,
            title=title.strip() or "untitled note",
            object_key=f"knowledge/{kb_id}/text/{digest}.txt",
            content_hash=digest,
            content_type=CONTENT_TYPES[".txt"],
            size_bytes=len(content.encode("utf-8")),
            page_count=1,
            text_content=content,
            created_by=created_by,
        )


__all__ = ["KnowledgeService", "InvalidDocument"]
