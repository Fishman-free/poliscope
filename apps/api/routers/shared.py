"""Public, unauthenticated read-only share links (A2).

A researcher mints an opaque token for a finished task; anyone holding the link
can open the *research output* without an account. This is the deliberate one
exception to owner isolation, and it is fenced three ways:

1. **Lookup is by token only** -- ``get_task_by_share_token`` never scopes by
   user, and the token is a 256-bit random URL-safe string (unguessable).
2. **Read-only** -- this router exposes only ``GET``; it cannot queue, edit,
   cancel, or adjudicate anything.
3. **Redacted** -- the snapshot goes through ``redact_public_snapshot``: cost
   usage and share metadata are dropped and signed URLs / local paths are
   scrubbed. The stored model api key is never in a snapshot to begin with.

No CurrentUserDep appears here on purpose: a bad or missing bearer token must
not block a public link (AGENTS.md: the researcher controls sharing, the shared
reader need not log in).
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from apps.api.dependencies import SessionDep
from apps.api.routers.workspace import _assemble_snapshot
from apps.api.schemas import WorkspaceSnapshot
from packages.research.repository import ResearchRepository, TaskNotFound

router = APIRouter()


@router.get("/{share_token}", response_model=WorkspaceSnapshot)
async def get_shared_workspace(
    share_token: str,
    session: SessionDep,
) -> WorkspaceSnapshot:
    """Return the redacted, read-only snapshot for a share token."""
    repository = ResearchRepository(session)
    try:
        task = await repository.get_task_by_share_token(share_token)
    except TaskNotFound as error:
        # Same 404 an unknown owned task gives: never reveal whether a token
        # merely does not exist versus was revoked.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="shared link not found or revoked",
        ) from error
    return await _assemble_snapshot(session, task, public=True)
