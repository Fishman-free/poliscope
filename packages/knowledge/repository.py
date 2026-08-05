"""Persistence for knowledge bases and their documents.

All reads and writes go through this repository so the API layer never
touches the tables directly. Deletion is reference-checked: a knowledge base
that tasks still point at, or a document that sources were built from, is
refused with a typed error rather than orphaned -- an uploaded document that
became evidence must stay traceable (CLAUDE.md 5.3's no-physical-deletion
spirit, applied at the collection boundary).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.knowledge.models import KnowledgeBaseModel, KnowledgeDocumentModel
from packages.papers.models import SourceModel
from packages.research.models import ResearchTaskModel


class KnowledgeBaseNotFound(Exception):
    """Raised when a knowledge base id does not exist."""


class DocumentNotFound(Exception):
    """Raised when a document id does not exist."""


class KnowledgeBaseInUse(Exception):
    """Raised when deleting a knowledge base that tasks still reference."""


class DocumentInUse(Exception):
    """Raised when deleting a document that sources were built from."""


@dataclass(frozen=True, slots=True)
class StoredKnowledgeBase:
    id: UUID
    name: str
    description: str | None
    created_by: str
    created_at: datetime
    document_count: int = 0


@dataclass(frozen=True, slots=True)
class StoredKnowledgeDocument:
    id: UUID
    knowledge_base_id: UUID
    title: str
    object_key: str
    content_hash: str
    content_type: str
    size_bytes: int
    page_count: int
    created_by: str
    created_at: datetime
    # Only loaded by get_document; list_documents deliberately leaves it out
    # so a document listing never drags full texts over the wire.
    text_content: str | None = None


class KnowledgeRepository:
    """One session's worth of knowledge-base operations."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_knowledge_base(
        self,
        name: str,
        description: str | None,
        created_by: str,
    ) -> StoredKnowledgeBase:
        row = KnowledgeBaseModel(
            id=uuid4(),
            name=name,
            description=description,
            created_by=created_by,
        )
        self._session.add(row)
        await self._session.flush()
        return StoredKnowledgeBase(
            id=row.id,
            name=row.name,
            description=row.description,
            created_by=row.created_by,
            created_at=row.created_at,
        )

    async def list_knowledge_bases(self) -> tuple[StoredKnowledgeBase, ...]:
        rows = (
            await self._session.execute(
                select(
                    KnowledgeBaseModel,
                    func.count(KnowledgeDocumentModel.id),
                )
                .outerjoin(
                    KnowledgeDocumentModel,
                    KnowledgeDocumentModel.knowledge_base_id
                    == KnowledgeBaseModel.id,
                )
                .group_by(KnowledgeBaseModel.id)
                .order_by(KnowledgeBaseModel.created_at.desc(), KnowledgeBaseModel.id)
            )
        ).all()
        return tuple(
            StoredKnowledgeBase(
                id=row.id,
                name=row.name,
                description=row.description,
                created_by=row.created_by,
                created_at=row.created_at,
                document_count=int(count),
            )
            for row, count in rows
        )

    async def get_knowledge_base(self, kb_id: UUID) -> StoredKnowledgeBase:
        row: KnowledgeBaseModel | None = await self._session.scalar(
            select(KnowledgeBaseModel).where(KnowledgeBaseModel.id == kb_id)
        )
        if row is None:
            raise KnowledgeBaseNotFound(kb_id)
        count = await self._session.scalar(
            select(func.count())
            .select_from(KnowledgeDocumentModel)
            .where(KnowledgeDocumentModel.knowledge_base_id == kb_id)
        )
        return StoredKnowledgeBase(
            id=row.id,
            name=row.name,
            description=row.description,
            created_by=row.created_by,
            created_at=row.created_at,
            document_count=int(count or 0),
        )

    async def add_document(
        self,
        kb_id: UUID,
        *,
        title: str,
        object_key: str,
        content_hash: str,
        content_type: str,
        size_bytes: int,
        page_count: int,
        text_content: str,
        created_by: str,
    ) -> StoredKnowledgeDocument:
        row = KnowledgeDocumentModel(
            id=uuid4(),
            knowledge_base_id=kb_id,
            title=title,
            object_key=object_key,
            content_hash=content_hash,
            content_type=content_type,
            size_bytes=size_bytes,
            page_count=page_count,
            text_content=text_content,
            created_by=created_by,
        )
        self._session.add(row)
        await self._session.flush()
        return StoredKnowledgeDocument(
            id=row.id,
            knowledge_base_id=row.knowledge_base_id,
            title=row.title,
            object_key=row.object_key,
            content_hash=row.content_hash,
            content_type=row.content_type,
            size_bytes=row.size_bytes,
            page_count=row.page_count,
            created_by=row.created_by,
            created_at=row.created_at,
        )

    async def list_documents(
        self, kb_id: UUID
    ) -> tuple[StoredKnowledgeDocument, ...]:
        rows = (
            await self._session.scalars(
                select(KnowledgeDocumentModel)
                .where(KnowledgeDocumentModel.knowledge_base_id == kb_id)
                .order_by(
                    KnowledgeDocumentModel.created_at.desc(),
                    KnowledgeDocumentModel.id,
                )
            )
        ).all()
        return tuple(
            StoredKnowledgeDocument(
                id=row.id,
                knowledge_base_id=row.knowledge_base_id,
                title=row.title,
                object_key=row.object_key,
                content_hash=row.content_hash,
                content_type=row.content_type,
                size_bytes=row.size_bytes,
                page_count=row.page_count,
                created_by=row.created_by,
                created_at=row.created_at,
            )
            for row in rows
        )

    async def get_document(self, doc_id: UUID) -> StoredKnowledgeDocument:
        row: KnowledgeDocumentModel | None = await self._session.scalar(
            select(KnowledgeDocumentModel).where(KnowledgeDocumentModel.id == doc_id)
        )
        if row is None:
            raise DocumentNotFound(doc_id)
        return StoredKnowledgeDocument(
            id=row.id,
            knowledge_base_id=row.knowledge_base_id,
            title=row.title,
            object_key=row.object_key,
            content_hash=row.content_hash,
            content_type=row.content_type,
            size_bytes=row.size_bytes,
            page_count=row.page_count,
            created_by=row.created_by,
            created_at=row.created_at,
            text_content=row.text_content,
        )

    async def delete_document(self, doc_id: UUID) -> None:
        row: KnowledgeDocumentModel | None = await self._session.scalar(
            select(KnowledgeDocumentModel).where(KnowledgeDocumentModel.id == doc_id)
        )
        if row is None:
            raise DocumentNotFound(doc_id)
        referenced = await self._session.scalar(
            select(func.count())
            .select_from(SourceModel)
            .where(SourceModel.knowledge_document_id == doc_id)
        )
        if referenced:
            raise DocumentInUse(doc_id)
        await self._session.delete(row)

    async def delete_knowledge_base(self, kb_id: UUID) -> None:
        row: KnowledgeBaseModel | None = await self._session.scalar(
            select(KnowledgeBaseModel).where(KnowledgeBaseModel.id == kb_id)
        )
        if row is None:
            raise KnowledgeBaseNotFound(kb_id)
        referenced = await self._session.scalar(
            select(func.count())
            .select_from(ResearchTaskModel)
            .where(ResearchTaskModel.knowledge_base_id == kb_id)
        )
        if referenced:
            raise KnowledgeBaseInUse(kb_id)
        documents = (
            await self._session.scalars(
                select(KnowledgeDocumentModel).where(
                    KnowledgeDocumentModel.knowledge_base_id == kb_id
                )
            )
        ).all()
        for document in documents:
            await self._session.delete(document)
        await self._session.delete(row)


__all__ = [
    "DocumentInUse",
    "DocumentNotFound",
    "KnowledgeBaseInUse",
    "KnowledgeBaseNotFound",
    "KnowledgeRepository",
    "StoredKnowledgeBase",
    "StoredKnowledgeDocument",
]
