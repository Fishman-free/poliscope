"""Multi-format text extraction for knowledge-base documents.

Knowledge bases are no longer PDF-only: the researcher uploads whatever they
have -- a paper, slides, a spreadsheet, raw notes -- and it is parsed to text
at ingest so the same keyword search and Level A acquisition pipeline can
serve every format.

Everything here is standard library (``zipfile`` + ``ElementTree`` for the
Office 2007+ container formats, which are zip archives of XML). Legacy binary
Office (.doc/.ppt/.xls) has no standard-library reader and is refused with a
message that says so -- CLAUDE.md 7: an unsupported format must stay visibly
unsupported, never be guessed at.
"""

from __future__ import annotations

import io
import xml.etree.ElementTree as ET
import zipfile
from pathlib import PurePosixPath

from packages.papers.parser import PageText, PdfExtractionError, extract_pages

# Office 2007+ namespace roots, matched by exact tag (ElementTree resolves
# namespace prefixes, so `&amp;` etc. are decoded by the XML parser, never by
# regex guesswork).
_WORDPROCESSING_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_DRAWINGML_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
_SPREADSHEETML_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"

_W_T = f"{{{_WORDPROCESSING_NS}}}t"
_W_P = f"{{{_WORDPROCESSING_NS}}}p"
_A_T = f"{{{_DRAWINGML_NS}}}t"
_S_T = f"{{{_SPREADSHEETML_NS}}}t"

# Plain-text formats, decoded rather than parsed (retrieval wants the full
# text; it does not need CSV field structure).
TEXT_EXTENSIONS = frozenset({".txt", ".md", ".csv"})

# Binary OLE compound formats. Unsupported by the standard library; refused
# up front so the user gets the reason instead of a mojibake parse.
LEGACY_OFFICE_EXTENSIONS = frozenset({".doc", ".dot", ".ppt", ".pps", ".xls"})


class InvalidDocument(Exception):
    """Raised when uploaded bytes cannot become a knowledge document."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


# Canonical suffix -> content type, shared by object-store keys (which carry
# the suffix) and the persisted metadata row.
CONTENT_TYPES = {
    ".pdf": "application/pdf",
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".csv": "text/csv",
    ".docx": (
        "application/vnd.openxmlformats-officedocument."
        "wordprocessingml.document"
    ),
    ".pptx": (
        "application/vnd.openxmlformats-officedocument."
        "presentationml.presentation"
    ),
    ".xlsx": (
        "application/vnd.openxmlformats-officedocument."
        "spreadsheetml.sheet"
    ),
}


def file_type(content: bytes, filename: str) -> tuple[str, str]:
    """``(suffix, content_type)`` for an already-validated upload.

    The suffix drives the object-store key, the content type the metadata
    row. PDF is keyed on magic bytes (a misnamed upload is still a PDF if it
    starts with ``%PDF``); every other format is keyed on its extension.
    """
    if content.startswith(b"%PDF"):
        return ".pdf", CONTENT_TYPES[".pdf"]
    suffix = PurePosixPath(filename.lower()).suffix
    return suffix, CONTENT_TYPES.get(suffix, "application/octet-stream")


def extract_text(
    content: bytes, filename: str
) -> tuple[list[PageText], int]:
    """Extract page/block text from any supported format.

    Returns ``(blocks, page_count)`` where blocks feed the search index and
    the Level A extraction pipeline exactly like PDF pages do. PDF keeps its
    page granularity; the Office formats are chunked per paragraph (docx),
    per slide (pptx), or as one block (xlsx / plain text), and ``page_count``
    reports the chunk count -- an honest coarse block is preferred over a
    fabricated page number (CLAUDE.md 7).
    """
    suffix = PurePosixPath(filename.lower()).suffix
    if content.startswith(b"%PDF") or suffix == ".pdf":
        try:
            pages = extract_pages(content)
        except PdfExtractionError as error:
            raise InvalidDocument(f"pdf parsing failed: {error}") from error
        if not pages:
            raise InvalidDocument("pdf produced no extractable text")
        return pages, len(pages)
    if suffix in LEGACY_OFFICE_EXTENSIONS:
        raise InvalidDocument(
            "legacy office format cannot be parsed; "
            "save as .docx/.pptx/.xlsx and retry"
        )
    if suffix in TEXT_EXTENSIONS:
        return [_decode_text(content)], 1
    if suffix == ".docx":
        blocks = _docx_text(content)
        return blocks, len(blocks)
    if suffix == ".pptx":
        slides = _pptx_text(content)
        return slides, len(slides)
    if suffix == ".xlsx":
        return _xlsx_text(content), 1
    raise InvalidDocument(
        f"unsupported file type{f' ({suffix})' if suffix else ''}"
    )


def _decode_text(content: bytes) -> PageText:
    """UTF-8 (BOM-tolerant) first, then GBK -- the codepage Chinese-office
    exports still use -- then refuse rather than silently mojibake."""
    for encoding in ("utf-8-sig", "gbk"):
        try:
            return PageText(page_number=1, text=content.decode(encoding))
        except UnicodeDecodeError:
            continue
    raise InvalidDocument("text file is neither valid UTF-8 nor GBK")


def _docx_paragraphs(xml: bytes) -> list[str]:
    """One text chunk per ``<w:p>`` paragraph, joining its ``<w:t>`` runs."""
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as error:
        raise InvalidDocument(f"docx parsing failed: {error}") from error
    paragraphs: list[str] = []
    for paragraph in root.iter(_W_P):
        chunk = "".join(node.text or "" for node in paragraph.iter(_W_T))
        if chunk.strip():
            paragraphs.append(chunk)
    return paragraphs


def _pptx_text(content: bytes) -> list[PageText]:
    """One chunk per slide, slides ordered by their number in the archive."""
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            slide_names = [
                name
                for name in archive.namelist()
                if name.startswith("ppt/slides/slide")
                and name.endswith(".xml")
            ]
            slide_names.sort(
                key=lambda name: int(
                    name.removeprefix("ppt/slides/slide").removesuffix(".xml")
                )
            )
            if not slide_names:
                raise InvalidDocument("pptx contains no slides")
            slides: list[PageText] = []
            for name in slide_names:
                root = ET.fromstring(archive.read(name))
                chunk = "".join(
                    node.text or "" for node in root.iter(_A_T)
                )
                if chunk.strip():
                    slides.append(
                        PageText(
                            page_number=int(
                                name.removeprefix("ppt/slides/slide")
                                .removesuffix(".xml")
                            ),
                            text=chunk,
                        )
                    )
    except zipfile.BadZipFile as error:
        raise InvalidDocument(f"pptx parsing failed: {error}") from error
    except ET.ParseError as error:
        raise InvalidDocument(f"pptx parsing failed: {error}") from error
    if not slides:
        raise InvalidDocument("pptx produced no extractable text")
    return slides


def _xlsx_text(content: bytes) -> list[PageText]:
    """All strings from sharedStrings plus any inline cell strings, in one
    block -- spreadsheets are tabular, and the keyword search wants the
    whole cell corpus rather than an invented row layout."""
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            strings: list[str] = []
            for name in archive.namelist():
                if not name.endswith(".xml"):
                    continue
                if name == "xl/sharedStrings.xml" or name.startswith(
                    "xl/worksheets/"
                ):
                    root = ET.fromstring(archive.read(name))
                    strings.extend(
                        node.text or "" for node in root.iter(_S_T)
                    )
    except zipfile.BadZipFile as error:
        raise InvalidDocument(f"xlsx parsing failed: {error}") from error
    except ET.ParseError as error:
        raise InvalidDocument(f"xlsx parsing failed: {error}") from error
    chunk = "\n".join(item for item in strings if item)
    if not chunk:
        raise InvalidDocument("xlsx produced no extractable text")
    return [PageText(page_number=1, text=chunk)]


def _docx_text(content: bytes) -> list[PageText]:
    """Word 2007+ documents: one text chunk per paragraph."""
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            try:
                xml = archive.read("word/document.xml")
            except KeyError as error:
                raise InvalidDocument(
                    "docx parsing failed: missing document.xml"
                ) from error
            paragraphs = _docx_paragraphs(xml)
    except zipfile.BadZipFile as error:
        raise InvalidDocument(f"docx parsing failed: {error}") from error
    if not paragraphs:
        raise InvalidDocument("docx produced no extractable text")
    return [
        PageText(page_number=index, text=text)
        for index, text in enumerate(paragraphs, start=1)
    ]


__all__ = ["CONTENT_TYPES", "InvalidDocument", "extract_text", "file_type"]
