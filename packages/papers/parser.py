from __future__ import annotations

import re
from dataclasses import dataclass


class PdfExtractionError(RuntimeError):
    """Raised when PDF text extraction fails."""


@dataclass(frozen=True, slots=True)
class PageText:
    page_number: int
    text: str


# A page whose embedded text layer carries fewer than this many *meaningful*
# characters cannot be a real body-text page: it is either a scanned/image
# page (the content lives in the raster, not the text layer) or a page that
# holds only a running header/footer. Such a page is worth OCR-ing -- but only
# when it actually carries raster images, so a native-text page with a short
# caption (which has no images) is never re-OCR'd into a garbled copy of text
# we already have (CLAUDE.md 7: prefer the text the PDF itself states).
_OCR_TRIGGER_CHARS = 100

# Tesseract language packs to try, most-inclusive first. ``chi_sim+eng`` reads
# mixed Chinese/English pages (the common case for this deployment); ``eng``
# is the fallback for an image whose deployment did not install the Chinese
# pack. Each entry is tried in order until one yields text.
_OCR_LANGUAGES = ("chi_sim+eng", "eng")

# Raster resolution handed to the OCR engine. Higher is more accurate on the
# small type academic print uses but slower; 300 is a reasonable floor.
_OCR_DPI = 300


def _meaningful_chars(text: str) -> int:
    """Count alphanumeric characters (CJK ideographs included), skipping
    whitespace and punctuation -- the characters that signal real content."""
    return sum(1 for char in text if char.isalnum())


def _page_has_images(page: object) -> bool:
    """True when the page references at least one raster image.

    A scan failure here must not fail extraction, so it degrades to ``False``
    (meaning: keep the text layer and skip OCR for this page).
    """
    get_images = getattr(page, "get_images", None)
    if get_images is None:
        return False
    try:
        return bool(get_images(full=True))
    except Exception:  # noqa: BLE001 -- a broken image table is not a text failure
        return False


def _ocr_page(page: object) -> str | None:
    """OCR a page through PyMuPDF's built-in Tesseract bridge, or ``None``.

    Returns ``None`` when OCR is unavailable (no Tesseract), when every
    language pack fails to load, or when the page yields no text -- the caller
    then keeps the (possibly sparse) text layer rather than fabricating text
    (CLAUDE.md 7: an unreadable page must stay visibly unreadable).
    """
    get_textpage_ocr = getattr(page, "get_textpage_ocr", None)
    get_text = getattr(page, "get_text", None)
    if get_textpage_ocr is None or get_text is None:
        return None
    for language in _OCR_LANGUAGES:
        try:
            textpage = get_textpage_ocr(language=language, dpi=_OCR_DPI)
            text = get_text("text", textpage=textpage)
        except Exception:  # noqa: BLE001 -- any OCR failure just falls through
            continue
        if isinstance(text, str) and text.strip():
            return text
    return None


def extract_pages(content: bytes) -> list[PageText]:
    """Extract page text from a PDF's bytes.

    Uses PyMuPDF when available; otherwise raises PdfExtractionError so
    callers can downgrade the evidence level instead of fabricating pages.

    Two methods are attempted, in order of trust (CLAUDE.md 7):

    1. **Text layer** -- ``get_text`` on the embedded fonts/text objects. This
       is exact and always preferred.
    2. **OCR** -- for a scanned/image page (sparse text layer *and* raster
       images), PyMuPDF's Tesseract bridge re-reads the raster. The OCR text
       replaces the text layer only when it carries *more* meaningful content,
       so a native-text page is never overwritten by an OCR pass.

    OCR failure is never an extraction failure: a page that cannot be OCR'd
    keeps whatever its text layer had (possibly just a running header). The
    caller still sees the sparse text and reports the gap honestly.

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
            if (
                _meaningful_chars(text) < _OCR_TRIGGER_CHARS
                and _page_has_images(page)
            ):
                ocr_text = _ocr_page(page)
                if ocr_text is not None and _meaningful_chars(
                    ocr_text
                ) > _meaningful_chars(text):
                    text = ocr_text
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
