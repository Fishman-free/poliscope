"""Ingestion side of a knowledge base: bytes in, parsed text stored.

The repository persists rows; this service owns the pipeline that fills them
-- size/magic validation, object-store write, PDF page extraction -- so the
API layer stays a thin mapping from HTTP to domain operations. `knowledge`
imports `papers` (parser, object_store) one-way; `papers` never imports
`knowledge`, keeping the module boundary acyclic.
"""

from __future__ import annotations

from uuid import UUID

from packages.knowledge.repository import KnowledgeRepository
from packages.knowledge.repository import StoredKnowledgeDocument
from packages.papers.object_store import PrivateObjectStore
from packages.papers.parser import PdfExtractionError, extract_pages

MAX_UPLOAD_BYTES = 20 * 1024 * 1024


class InvalidDocument(Exception):
    """Raised when uploaded bytes cannot become a knowledge document."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


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
        """Validate, store, parse, and persist one uploaded PDF.

        Never raises on the caller's behalf beyond :class:`InvalidDocument`:
        an empty file, non-PDF bytes, an oversized file, or a PDF that yields
        no text are all refused here with the reason attached, so the API can
        map them to 422s without guessing what happened (CLAUDE.md 7).
        """
        if not content:
            raise InvalidDocument("uploaded file is empty")
        if not content.startswith(b"%PDF"):
            raise InvalidDocument("uploaded file is not a PDF")
        if len(content) > MAX_UPLOAD_BYTES:
            raise InvalidDocument("uploaded file exceeds the 20 MB limit")
        try:
            pages = extract_pages(content)
        except PdfExtractionError as error:
            raise InvalidDocument(f"pdf parsing failed: {error}") from error
        if not pages:
            raise InvalidDocument("pdf produced no extractable text")

        stored = self._object_store.store_named(f"knowledge/{kb_id}", content)
        text_content = "\n\n".join(page.text for page in pages)
        return await self._repository.add_document(
            kb_id,
            title=filename,
            object_key=stored.object_key,
            content_hash=stored.content_hash,
            content_type=stored.content_type,
            size_bytes=stored.size_bytes,
            page_count=len(pages),
            text_content=text_content,
            created_by=created_by,
        )


__all__ = ["InvalidDocument", "KnowledgeService"]
