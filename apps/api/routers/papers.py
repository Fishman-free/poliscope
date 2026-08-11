"""Uploaded-paper ingestion endpoint (multi-format since round-7).

An uploaded paper has no DOI, so it cannot ride in on ``ResearchContract.
user_evidence.pdf_object_ids`` at task-creation time the way a DOI or BibTeX
entry can: ``ObjectModel.task_id`` is a NOT NULL foreign key, so the task must
already exist before an object can reference it. The flow is therefore:
create the task first (with an empty ``pdf_object_ids``) -> upload against
that task's id -> this endpoint patches the task's stored ``user_evidence`` ->
``confirm-claims``/``queue`` as usual. This is a deliberate, recorded
deviation from a task-agnostic upload endpoint (CLAUDE.md 17): the schema's
own constraint made the alternative impossible, not a preference.

Formats: PDF (magic bytes), DOCX/PPTX/XLSX/HTML/TXT/MD/CSV (extension).
The file is validated *by extracting it* -- if the bytes cannot become text
the upload is refused here with the reason, not left to fail deep in the
worker's extraction pass. Legacy binary Office (.doc/.ppt/.xls) and unknown
extensions are refused with a message that says how to fix it (CLAUDE.md 7:
an unsupported format must stay visibly unsupported, never be guessed at).
"""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, UploadFile, status

from apps.api.dependencies import CurrentUserDep, ObjectStoreDep, SessionDep
from packages.knowledge.extractors import InvalidDocument, extract_text, file_type
from packages.papers.models import ObjectModel
from packages.research.repository import ResearchRepository, TaskNotFound
from packages.research.service import ResearchService

router = APIRouter()

TASK_NOT_FOUND = "unknown task"
EMPTY_FILE = "上传的文件为空，请重新选择文件"
UNPARSEABLE = "无法读取上传的文件"
TOO_LARGE = "上传的文件超过 20 MB 上限"
# Shown as a suffix on a refused upload so the researcher knows the failure
# is about the file, not about the system (round-10 report: "document closed
# or encrypted" read as a broken uploader). Appended after UNPARSEABLE.
UNPARSEABLE_HINT = (
    "如果这是 PDF 且设置了打开密码，请先在本地解密（「打印为 PDF」或阅读器中"
    "移除密码）后再上传；只读权限加密的 PDF 可以直接上传。"
)

# Same ceiling as the nginx `client_max_body_size 20m;` in apps/web/nginx.conf:
# nginx rejects the request before it reaches us, this check is the
# authoritative layer for deployments without that nginx in front.
MAX_UPLOAD_BYTES = 20 * 1024 * 1024

# Shown in the frontend's file-picker accept attribute and error copy; keep in
# sync with extract_text's supported formats.
SUPPORTED_FORMATS = "PDF, DOCX, PPTX, XLSX, HTML, TXT, MD, CSV"


@router.post("/{task_id}/papers/upload", status_code=status.HTTP_201_CREATED)
async def upload_paper(
    task_id: UUID,
    session: SessionDep,
    object_store: ObjectStoreDep,
    current_user: CurrentUserDep,
    file: UploadFile,
) -> dict[str, Any]:
    """Store an uploaded paper and attach it to a task's user evidence.

    Never persists the raw bytes anywhere but the private object store, and
    never logs or returns them (CLAUDE.md 16).
    """
    repository = ResearchRepository(session)
    try:
        await repository.get_task(task_id, current_user.id)
    except TaskNotFound as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"{TASK_NOT_FOUND} {task_id}",
        ) from error

    filename = file.filename or "paper.pdf"
    content = await file.read()
    if not content:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=EMPTY_FILE,
        )
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=TOO_LARGE,
        )
    # The format gate is *extraction*, not a header: a client-controlled
    # content_type is not evidence of what the bytes are, and the worker's
    # Level A extraction depends on this file actually parsing. Trying the
    # extraction here refuses an unreadable upload with its reason up front
    # instead of a silent gap deep in the council run.
    try:
        extract_text(content, filename)
    except InvalidDocument as error:
        # error.reason already carries a concrete, user-facing reason (e.g. the
        # password-protected-PDF message from packages/papers/parser.py); the
        # hint tells the researcher the fix without burying the specific cause.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                f"{UNPARSEABLE}：{error.reason}。{UNPARSEABLE_HINT}"
            ),
        ) from error

    suffix, content_type = file_type(content, filename)
    stored = object_store.store_named(
        f"tasks/{task_id}",
        content,
        suffix=suffix,
        content_type=content_type,
    )
    object_id = uuid4()
    session.add(
        ObjectModel(
            id=object_id,
            task_id=task_id,
            object_key=stored.object_key,
            content_hash=stored.content_hash,
            encryption=stored.encryption,
            content_type=stored.content_type,
            size_bytes=stored.size_bytes,
            file_name=filename,
        )
    )
    await session.flush()
    await ResearchService(ResearchRepository(session)).add_pdf_object_id(
        task_id, object_id
    )

    return {"object_id": str(object_id), **object_store.public_dto(stored)}
