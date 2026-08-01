from __future__ import annotations

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
    """
    try:
        import fitz  # type: ignore[import-untyped]  # PyMuPDF
    except ImportError as exc:
        raise PdfExtractionError("PyMuPDF unavailable") from exc

    pages: list[PageText] = []
    try:
        document = fitz.open(stream=content, filetype="pdf")
    except Exception as exc:
        raise PdfExtractionError("failed to open PDF") from exc

    try:
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
