"""Multi-format extraction for knowledge-base documents.

Every supported format is exercised with in-memory bytes (zip archives built
on the fly for the Office 2007+ formats), so the tests pin the parsing
contract without any fixture files: PDF/TXT/MD/CSV/DOCX/PPTX/XLSX in, text and
a page count out; legacy Office and unknown types refused with a reason.
"""

from __future__ import annotations

import io
import zipfile

import pytest

from packages.knowledge.extractors import InvalidDocument, extract_text

# Namespace roots, kept here so the fixtures mirror real files closely enough
# for ElementTree's namespace-aware iteration to match.
_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
_P_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"
_S_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"


def _zip_bytes(entries: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in entries.items():
            archive.writestr(name, content.encode("utf-8"))
    return buffer.getvalue()


def _docx_bytes(*paragraphs: str) -> bytes:
    body = "".join(
        f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p>" for text in paragraphs
    )
    return _zip_bytes(
        {
            "word/document.xml": (
                f'<?xml version="1.0"?>'
                f'<w:document xmlns:w="{_W_NS}"><w:body>{body}</w:body></w:document>'
            )
        }
    )


def _pptx_bytes(*slides: str) -> bytes:
    entries: dict[str, str] = {}
    for index, text in enumerate(slides, start=1):
        entries[f"ppt/slides/slide{index}.xml"] = (
            f'<?xml version="1.0"?>'
            f'<p:sld xmlns:p="{_P_NS}" xmlns:a="{_A_NS}">'
            f"<p:cSld><p:spTree><p:sp><p:txBody><a:p><a:r><a:t>{text}</a:t></a:r>"
            f"</a:p></p:txBody></p:sp></p:spTree></p:cSld></p:sld>"
        )
    return _zip_bytes(entries)


def _xlsx_bytes(*strings: str) -> bytes:
    sst = "".join(f"<si><t>{text}</t></si>" for text in strings)
    return _zip_bytes(
        {
            "xl/sharedStrings.xml": (
                f'<?xml version="1.0"?><sst xmlns="{_S_NS}">{sst}</sst>'
            ),
            "xl/worksheets/sheet1.xml": (
                f'<?xml version="1.0"?><worksheet xmlns="{_S_NS}">'
                '<sheetData><row><c r="A1" t="s"><v>0</v></c></row></sheetData>'
                "</worksheet>"
            ),
        }
    )


def _extract(content: bytes, filename: str) -> str:
    blocks, _count = extract_text(content, filename)
    return "\n".join(page.text for page in blocks)


def test_txt_utf8_with_bom() -> None:
    text = "hello 中文"
    blocks, count = extract_text(("﻿" + text).encode("utf-8"), "note.txt")
    assert count == 1
    assert blocks[0].text == text


def test_txt_gbk_fallback() -> None:
    text = "中文备注"
    blocks, count = extract_text(text.encode("gbk"), "note.txt")
    assert count == 1
    assert blocks[0].text == text


def test_txt_that_is_neither_utf8_nor_gbk_is_refused() -> None:
    with pytest.raises(InvalidDocument, match="neither valid UTF-8 nor GBK"):
        extract_text(b"\xff\xfe\xfd invalid sequence \xff", "note.txt")


def test_csv_is_treated_as_plain_text() -> None:
    blocks, count = extract_text(b"name,score\nada,1.0", "data.csv")
    assert count == 1
    assert "name,score" in blocks[0].text


def test_docx_extracts_paragraphs_and_entities() -> None:
    blocks, count = extract_text(
        _docx_bytes("First paragraph.", "Second with &amp; entity."), "paper.docx"
    )
    assert count == 2
    assert blocks[0].text == "First paragraph."
    assert blocks[1].text == "Second with & entity."


def test_pptx_extracts_slides_in_order() -> None:
    blocks, count = extract_text(
        _pptx_bytes("Slide one", "Slide two"), "deck.pptx"
    )
    assert count == 2
    assert blocks[0].page_number == 1
    assert blocks[0].text == "Slide one"
    assert blocks[1].page_number == 2
    assert blocks[1].text == "Slide two"


def test_xlsx_extracts_shared_strings() -> None:
    blocks, count = extract_text(
        _xlsx_bytes("Cell A", "Cell B"), "table.xlsx"
    )
    assert count == 1
    assert "Cell A" in blocks[0].text
    assert "Cell B" in blocks[0].text


def test_legacy_office_formats_are_refused_with_guidance() -> None:
    for filename in ("old.doc", "old.ppt", "old.xls"):
        with pytest.raises(InvalidDocument, match="save as .docx/.pptx/.xlsx"):
            extract_text(b"\xd0\xcf\x11\xe0 legacy ole bytes", filename)


def test_unknown_extension_is_refused() -> None:
    with pytest.raises(InvalidDocument, match="unsupported file type"):
        extract_text(b"anything", "file.xyz")


def test_bad_zip_under_a_docx_name_is_refused() -> None:
    with pytest.raises(InvalidDocument, match="docx parsing failed"):
        extract_text(b"not a zip archive at all", "broken.docx")


def test_empty_pptx_archive_is_refused() -> None:
    with pytest.raises(InvalidDocument, match="contains no slides"):
        extract_text(_zip_bytes({}), "empty.pptx")


def test_pdf_path_still_parses_pages() -> None:
    import fitz  # type: ignore[import-untyped]

    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), "A real PDF page.")
    content = bytes(document.tobytes())

    blocks, count = extract_text(content, "paper.pdf")
    assert count == 1
    assert "A real PDF page." in blocks[0].text


def test_pdf_magic_wins_over_wrong_extension() -> None:
    # Content that starts with %PDF is treated as a PDF even when the
    # filename says otherwise -- but garbage PDF bytes are refused honestly.
    with pytest.raises(InvalidDocument, match="pdf parsing failed"):
        extract_text(b"%PDF-1.7 this is not really a pdf", "file.txt")
