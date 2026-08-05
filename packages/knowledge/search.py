"""Keyword retrieval over one knowledge base's documents.

The researcher-confirmed scope is keyword search, no vectors: Postgres full
text (``search_vector``) plus an ILIKE substring branch, scored and capped.
The FTS branch handles English tokenisation; the ILIKE branch is what makes
Chinese search work at all, because ``to_tsvector('simple', ...)`` turns an
unsegmented Chinese sentence into a single token that ``@@`` cannot match
substring-wise. That limitation is deliberate and documented (no jieba --
zero new Python dependencies), and the UI/README state it.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import func, text
from sqlalchemy.ext.asyncio import AsyncSession

from packages.knowledge.models import KnowledgeDocumentModel


@dataclass(frozen=True, slots=True)
class KnowledgeHit:
    document_id: UUID
    document_title: str
    snippet: str
    score: float


def _ilike_count(text_value: str, query: str) -> int:
    """How many non-overlapping times ``query`` appears in ``text_value``.

    Pure Python so unit tests can pin it without a database; the SQL query
    computes the same value with ``length()`` arithmetic.
    """
    if not query:
        return 0
    lowered = text_value.lower()
    needle = query.lower()
    return lowered.count(needle)


def _snippet(text_value: str, query: str, width: int = 150) -> str:
    """A window around the first hit, or a prefix when nothing matched.

    The snippet is for a human to judge relevance, never for citation --
    quotes in reports always come from the document itself via the finding
    extractor, so there is no precision pressure on the window boundaries.
    """
    index = text_value.lower().find(query.lower())
    if index < 0:
        return text_value[:width]
    start = max(0, index - width // 2)
    end = min(len(text_value), index + len(query) + width // 2)
    snippet = text_value[start:end]
    if start > 0:
        snippet = "…" + snippet
    if end < len(text_value):
        snippet = snippet + "…"
    return snippet


class KnowledgeBaseSearch:
    """One knowledge base's keyword search, over one session."""

    def __init__(self, session: AsyncSession, kb_id: UUID) -> None:
        self._session = session
        self._kb_id = kb_id

    async def search(
        self,
        query: str,
        limit: int = 5,
    ) -> tuple[KnowledgeHit, ...]:
        """Score every document by FTS rank plus substring count, cap the top k.

        Scoring: ``ts_rank`` (0..1 against this query's best match) doubled,
        plus the ILIKE occurrence count -- a Chinese phrase that FTS cannot
        tokenise still scores through the substring branch. ``plainto_tsquery``
        on an empty or punctuation-only query is safe (matches nothing), and
        zero hits are returned as an empty tuple rather than fabricated.
        """
        if not query.strip():
            return ()
        sql = text(
            """
            SELECT id, title, text_content,
                   ts_rank(search_vector, plainto_tsquery('simple', :q)) * 2
                     + (length(text_content)
                        - length(replace(lower(text_content), lower(:q), '')))
                     / greatest(length(:q), 1) AS score
            FROM knowledge_documents
            WHERE knowledge_base_id = :kb_id
              AND (
                search_vector @@ plainto_tsquery('simple', :q)
                OR text_content ILIKE '%' || :q || '%'
              )
            ORDER BY score DESC, id
            LIMIT :limit
            """
        )
        rows = await self._session.execute(
            sql,
            {"q": query, "kb_id": self._kb_id, "limit": limit},
        )
        hits: list[KnowledgeHit] = []
        for row in rows:
            hits.append(
                KnowledgeHit(
                    document_id=row.id,
                    document_title=row.title,
                    snippet=_snippet(row.text_content, query),
                    score=float(row.score),
                )
            )
        return tuple(hits)


__all__ = ["KnowledgeBaseSearch", "KnowledgeHit", "_ilike_count", "_snippet"]
