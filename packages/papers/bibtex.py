"""Best-effort DOI extraction from BibTeX text, with no parsing dependency.

The web form lets a researcher paste raw BibTeX entries as user evidence. The
contract stores the text verbatim, but nothing downstream reads it -- a
"stored" BibTeX entry that never informed acquisition would be the silent
data loss CLAUDE.md 7 forbids. Rather than adding a full BibTeX parser for an
MVP (YAGNI, and zero new dependencies is a standing rule), this module pulls
the DOI out of the two shapes a bibliographic entry reliably contains: the
``doi`` field's value, and the first DOI-looking token in the text. It is
honest about being best-effort: an entry that yields nothing is simply not
consumed, never fabricated into a DOI that was not there.
"""

from __future__ import annotations

import re

from packages.tools.adapters.normalization import normalize_doi

# A DOI is 10.<4-9 digits>/<anything except whitespace>. The value sits inside
# a BibTeX field, so surrounding quotes/braces/commas must not be captured.
_FIELD_VALUE = re.compile(
    r"doi\s*=\s*[\"{}]?\s*(10\.\d{4,9}/[^\s\"{}]+?)\s*[\"{}]?,",
    re.IGNORECASE,
)
# Fallback for a `doi` field written without a trailing comma (last field in
# the entry, or malformed), or a DOI mentioned anywhere else in the text.
_ANYWHERE = re.compile(r"10\.\d{4,9}/[^\s\"{}]+", re.IGNORECASE)


def extract_dois_from_bibtex(entries: str) -> tuple[str, ...]:
    """Return the unique, normalised DOIs found in ``entries``.

    An entry contributes at most one DOI: its ``doi`` field when present,
    otherwise the first DOI-looking token. Duplicates are normalised and
    deduplicated in first-seen order, so a researcher pasting the same entry
    twice does not make the acquisition round spend twice on one paper.
    """
    if not entries:
        return ()
    seen: set[str] = set()
    result: list[str] = []
    for match in _FIELD_VALUE.finditer(entries):
        doi = normalize_doi(match.group(1))
        if doi and doi not in seen:
            seen.add(doi)
            result.append(doi)
    for match in _ANYWHERE.finditer(entries):
        doi = normalize_doi(match.group(0))
        if doi and doi not in seen:
            seen.add(doi)
            result.append(doi)
    return tuple(result)


__all__ = ["extract_dois_from_bibtex"]
