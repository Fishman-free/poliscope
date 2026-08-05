"""Knowledge-base management endpoints.

A knowledge base is the researcher's long-term memory: documents uploaded
once, parsed to text, and reusable across tasks (linked at task creation via
``knowledge_base_id``). Deletion is reference-checked -- a base that tasks
still point at, or a document that sources were built from, is refused with
409 so evidence stays traceable (CLAUDE.md 5.3's no-physical-deletion
discipline, applied at the collection boundary).
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, UploadFile, status

from apps.api.dependencies import CurrentUserDep, ObjectStoreDep, SessionDep
from packages.knowledge.repository import (
    DocumentInUse,
    DocumentNotFound,
    KnowledgeBaseInUse,
    KnowledgeBaseNotFound,
    KnowledgeRepository,
    StoredKnowledgeBase,
    StoredKnowledgeDocument,
)
from packages.knowledge.service import InvalidDocument, KnowledgeService

router = APIRouter()

# Preview text is truncated server-side; the client shows it read-only and
# never loads the full document over the wire for browsing.
PREVIEW_MAX_CHARS = 20_000


def _kb_dto(kb: StoredKnowledgeBase) -> dict[str, Any]:
    return {
        "id": str(kb.id),
        "name": kb.name,
        "description": kb.description,
        "created_at": kb.created_at.isoformat(),
        "document_count": kb.document_count,
    }


def _document_dto(doc: StoredKnowledgeDocument) -> dict[str, Any]:
    return {
        "document_id": str(doc.id),
        "title": doc.title,
        "size_bytes": doc.size_bytes,
        "page_count": doc.page_count,
        "content_type": doc.content_type,
        "created_at": doc.created_at.isoformat(),
    }


def _service(session: SessionDep, object_store: ObjectStoreDep) -> KnowledgeService:
    return KnowledgeService(KnowledgeRepository(session), object_store)


def _repo(session: SessionDep) -> KnowledgeRepository:
    return KnowledgeRepository(session)


@router.post("", status_code=status.HTTP_201_CREATED)
@router.post("/", status_code=status.HTTP_201_CREATED, include_in_schema=False)
async def create_knowledge_base(
    payload: dict[str, Any],
    session: SessionDep,
    current_user: CurrentUserDep,
) -> dict[str, Any]:
    name = str(payload.get("name") or "").strip()
    if not name:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="name must not be empty",
        )
    description = payload.get("description")
    created = await KnowledgeRepository(session).create_knowledge_base(
        name=name,
        description=(
            str(description).strip() if description is not None else None
        ),
        created_by=current_user.username,
        user_id=current_user.id,
    )
    await session.commit()
    return _kb_dto(created)


@router.get("")
@router.get("/", include_in_schema=False)
async def list_knowledge_bases(
    session: SessionDep,
    current_user: CurrentUserDep,
) -> list[dict[str, Any]]:
    bases = await KnowledgeRepository(session).list_knowledge_bases(
        current_user.id
    )
    return [_kb_dto(kb) for kb in bases]


@router.get("/{kb_id}")
async def get_knowledge_base(
    kb_id: UUID,
    session: SessionDep,
    current_user: CurrentUserDep,
) -> dict[str, Any]:
    repository = KnowledgeRepository(session)
    try:
        kb = await repository.get_knowledge_base(kb_id, current_user.id)
    except KnowledgeBaseNotFound as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"unknown knowledge base {kb_id}",
        ) from error
    documents = await repository.list_documents(kb_id)
    return {**_kb_dto(kb), "documents": [_document_dto(doc) for doc in documents]}


@router.post(
    "/{kb_id}/documents/upload", status_code=status.HTTP_201_CREATED
)
async def upload_document(
    kb_id: UUID,
    session: SessionDep,
    object_store: ObjectStoreDep,
    current_user: CurrentUserDep,
    file: UploadFile,
) -> dict[str, Any]:
    """Store, parse, and persist one uploaded document (PDF/TXT/MD/CSV/
    DOCX/PPTX/XLSX) into the knowledge base.

    Mirrors the task-PDF endpoint's discipline: never persist raw bytes
    anywhere but the private object store, never log or return them
    (CLAUDE.md 16). Validation (magic bytes, size, parseability) lives in
    KnowledgeService.ingest_document so the API stays a thin mapping.
    """
    repository = KnowledgeRepository(session)
    try:
        await repository.get_knowledge_base(kb_id, current_user.id)
    except KnowledgeBaseNotFound as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"unknown knowledge base {kb_id}",
        ) from error
    content = await file.read()
    try:
        doc = await _service(session, object_store).ingest_document(
            kb_id,
            filename=file.filename or "untitled",
            content=content,
            created_by="web",
        )
    except InvalidDocument as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=error.reason,
        ) from error
    await session.commit()
    return {
        **_document_dto(doc),
        "content_hash": doc.content_hash,
    }


@router.post(
    "/{kb_id}/documents/text", status_code=status.HTTP_201_CREATED
)
@router.post(
    "/{kb_id}/documents/text/",
    status_code=status.HTTP_201_CREATED,
    include_in_schema=False,
)
async def add_text_document(
    kb_id: UUID,
    payload: dict[str, Any],
    session: SessionDep,
    object_store: ObjectStoreDep,
    current_user: CurrentUserDep,
) -> dict[str, Any]:
    """Persist researcher-pasted text as a knowledge document.

    The plain-text sibling of the upload endpoint: same knowledge-base
    membership, same search indexing, same Level A treatment by the council
    -- the only difference is that the content arrives as text over JSON
    instead of bytes over multipart, so it never touches the object store.
    """
    title = str(payload.get("title") or "").strip()
    content = str(payload.get("content") or "")
    if not title:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="title must not be empty",
        )
    repository = KnowledgeRepository(session)
    try:
        await repository.get_knowledge_base(kb_id, current_user.id)
    except KnowledgeBaseNotFound as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"unknown knowledge base {kb_id}",
        ) from error
    try:
        doc = await _service(session, object_store).add_text_document(
            kb_id,
            title=title,
            content=content,
            created_by="web",
        )
    except InvalidDocument as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=error.reason,
        ) from error
    await session.commit()
    return {
        **_document_dto(doc),
        "content_hash": doc.content_hash,
    }


@router.get("/{kb_id}/documents/{doc_id}")
async def get_document(
    kb_id: UUID,
    doc_id: UUID,
    session: SessionDep,
    current_user: CurrentUserDep,
) -> dict[str, Any]:
    """One document's metadata plus a truncated preview of its text.

    ``truncated`` is explicit: a scan-heavy PDF is cut at 20k characters and
    the client is told so, rather than silently receiving a partial text
    that looks complete (CLAUDE.md 7).
    """
    repository = KnowledgeRepository(session)
    try:
        await repository.get_knowledge_base(kb_id, current_user.id)
        doc = await repository.get_document(doc_id)
    except (KnowledgeBaseNotFound, DocumentNotFound) as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="unknown document",
        ) from error
    text = doc.text_content or ""
    truncated = len(text) > PREVIEW_MAX_CHARS
    return {
        **_document_dto(doc),
        "text": text[:PREVIEW_MAX_CHARS],
        "truncated": truncated,
    }


@router.delete("/{kb_id}/documents/{doc_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    kb_id: UUID,
    doc_id: UUID,
    session: SessionDep,
    current_user: CurrentUserDep,
) -> None:
    repository = KnowledgeRepository(session)
    try:
        # Ownership runs base -> document: a base that is not the caller's is
        # 404 before the document is even looked at.
        await repository.get_knowledge_base(kb_id, current_user.id)
        await repository.delete_document(doc_id)
    except KnowledgeBaseNotFound as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"unknown knowledge base {kb_id}",
        ) from error
    except DocumentNotFound as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="unknown document",
        ) from error
    except DocumentInUse as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="document is referenced by research evidence",
        ) from error
    await session.commit()


@router.delete("/{kb_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_knowledge_base(
    kb_id: UUID,
    session: SessionDep,
    current_user: CurrentUserDep,
) -> None:
    repository = KnowledgeRepository(session)
    try:
        await repository.delete_knowledge_base(kb_id, current_user.id)
    except KnowledgeBaseNotFound as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"unknown knowledge base {kb_id}",
        ) from error
    except KnowledgeBaseInUse as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="knowledge base is referenced by research tasks",
        ) from error
    await session.commit()
