"""Normalize a free-text academic search query before it hits a vendor.

Seats (and the adversarial-retrieval generator) produce free-form strings.
OpenAlex / Crossref / Semantic Scholar index English bibliographic metadata;
a Chinese essay, a claim UUID, or a justification sentence attached to a DOI
turns into a random keyword hit -- the production failure that retrieved
nuclear-plant papers for a question about whether love is a human necessity.

This module is the last line of defence *before* the HTTP call. It never
invents a query (CLAUDE.md 7): if nothing searchable remains, it returns
``None`` and the caller records an honest miss.
"""

from __future__ import annotations

import re
from uuid import UUID

# A DOI already extracted by CandidatePool; leave those paths alone.
_DOI_RE = re.compile(r"10\.\d{4,9}/[0-9A-Za-z._;()/:+-]+")
_UUID_RE = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)
# CJK ideographs / kana / hangul -- academic vendors do not rank these.
_CJK_RE = re.compile(r"[　-鿿가-힯]+")
# A prefix seats used to attach ("claim <uuid>: ...").
_CLAIM_PREFIX_RE = re.compile(r"^\s*claim\s+[0-9a-fA-F-]+:\s*", re.IGNORECASE)

_MAX_QUERY_CHARS = 200


def sanitize_search_query(raw: str) -> str | None:
    """Return a vendor-safe search phrase, or ``None`` when nothing remains.

    A DOI-shaped string is returned as-is (the DOI path handles it). Everything
    else is stripped of UUIDs and CJK, collapsed, and length-capped.
    """
    text = (raw or "").strip()
    if not text:
        return None
    if _DOI_RE.search(text):
        return text
    text = _CLAIM_PREFIX_RE.sub("", text)
    text = _UUID_RE.sub(" ", text)
    text = _CJK_RE.sub(" ", text)
    text = " ".join(text.split())
    if not text:
        return None
    return text[:_MAX_QUERY_CHARS]


def is_uuid_only(raw: str) -> bool:
    """True when the string is just a UUID (optionally with 'claim ' prefix)."""
    text = (raw or "").strip()
    try:
        UUID(text)
        return True
    except ValueError:
        return False


__all__ = ["sanitize_search_query", "is_uuid_only"]
