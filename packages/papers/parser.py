from __future__ import annotations

import re
from dataclasses import dataclass


class PdfExtractionError(RuntimeError):
    """Raised when PDF text extraction fails."""


@dataclass(frozen=True, slots=True)
class PageText:
    page_number: int
    text: str


def extract_pages(content: bytes) -> list[PageText]:
    """Extract page text from a PDF's bytes.

    Uses PyMuPDF when available; otherwise raises PdfExtractionError so
    callers can downgrade the evidence level instead of fabricating pages.

    A PDF carrying an owner password but no user password (the common
    "permissions-only" copy-protection on journal PDFs) reads normally --
    ``needs_pass`` is False and ``get_text`` works. A PDF with a *user*
    password cannot be read without that password: this is the encryption
    itself, not a tooling gap, so the error says so in plain language
    (CLAUDE.md 7) instead of surfacing PyMuPDF's internal
    "document closed or encrypted" as if the file were merely damaged.
    """
    try:
        import fitz  # type: ignore[import-untyped]  # PyMuPDF
    except ImportError as exc:
        raise PdfExtractionError("PyMuPDF unavailable") from exc

    try:
        document = fitz.open(stream=content, filetype="pdf")
    except Exception as exc:
        raise PdfExtractionError("failed to open PDF") from exc

    pages: list[PageText] = []
    try:
        # authenticate("") unlocks a permissions-only PDF automatically; a
        # user-password PDF stays locked and cannot be read without the
        # password (return value 0 = still encrypted after the attempt).
        if document.needs_pass and not document.authenticate(""):
            raise PdfExtractionError(
                "PDF 有打开密码保护，无法读取正文；请在本地解密后再上传"
                "（可用「打印为 PDF」或在 PDF 阅读器中移除密码）"
            )
        for index, page in enumerate(document, start=1):
            text = page.get_text("text")
            pages.append(PageText(page_number=index, text=text))
    finally:
        document.close()
    return pages


def locate_quote(pages: list[PageText], quote: str) -> int | None:
    """Return the first page number containing the exact quote, or None."""
    normalized = quote.strip()
    for page in pages:
        if normalized in page.text:
            return page.page_number
    return None


# Each pattern targets a distinct, unambiguous repository prefix or hostname
# (Harvard Dataverse's 10.7910/DVN, Dryad's 10.5061/dryad, Zenodo's 10.5281/
# zenodo or zenodo.org, ICPSR's own numbering, OSF's short slugs) so match
# order never matters -- no two patterns can both fire on the same text.
_DATASET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "ICPSR",
        re.compile(
            r"ICPSR\s*(?:study\s*(?:number|no\.?)\s*)?#?\s*(\d{4,6}(?:-v\d+)?)",
            re.IGNORECASE,
        ),
    ),
    ("OSF", re.compile(r"osf\.io/([a-z0-9]{5,8})", re.IGNORECASE)),
    ("Dryad", re.compile(r"(10\.5061/dryad\.[^\s,;)\]]+)", re.IGNORECASE)),
    (
        "Zenodo",
        re.compile(
            r"(?:zenodo\.org/record/(\d+)|10\.5281/zenodo\.(\d+))", re.IGNORECASE
        ),
    ),
    ("Dataverse", re.compile(r"(10\.7910/DVN/[^\s,;)\]]+)", re.IGNORECASE)),
)


def detect_dataset_identifier(pages: list[PageText]) -> str | None:
    """Scan full text for a known dataset-accession pattern.

    Deterministic pattern matching only -- CLAUDE.md 7 forbids fabricating a
    dataset identifier the source text does not actually contain, so this
    never asks a model to infer one. Recognizes a small, named set of public
    repository accession conventions (ICPSR, OSF, Dataverse, Dryad, Zenodo)
    commonly found in a paper's "Data Availability" statement; anything else
    stays ``None`` rather than guessing.
    """
    for repository, pattern in _DATASET_PATTERNS:
        for page in pages:
            match = pattern.search(page.text)
            if match:
                identifier = next(g for g in match.groups() if g)
                return f"{repository}:{identifier.rstrip('.,;)')}"
    return None
