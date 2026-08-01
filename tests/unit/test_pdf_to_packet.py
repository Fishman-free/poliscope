from __future__ import annotations

from typing import Any

import pytest

from packages.papers.parser import PageText, locate_quote


def test_locate_quote_returns_first_page() -> None:
    pages = [
        PageText(page_number=1, text="intro"),
        PageText(page_number=2, text="the exact quote is here"),
        PageText(page_number=3, text="the exact quote is here too"),
    ]
    assert locate_quote(pages, "the exact quote") == 2


def test_locate_quote_returns_none_when_missing() -> None:
    pages = [PageText(page_number=1, text="unrelated")]
    assert locate_quote(pages, "missing") is None


def test_extract_pages_raises_when_pymupdf_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import builtins
    import importlib

    import packages.papers.parser as parser_module

    real_import = builtins.__import__

    def fake_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "fitz":
            raise ImportError("simulated missing pymupdf")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    importlib.reload(parser_module)
    with pytest.raises(parser_module.PdfExtractionError):
        parser_module.extract_pages(b"%PDF-1.4 fake")
    importlib.reload(parser_module)  # restore


def test_build_packet_from_pages() -> None:
    from packages.papers.packet import build_packet

    pages = [
        PageText(page_number=1, text="Introduction."),
        PageText(page_number=2, text="Screen time correlates with anxiety."),
    ]
    source: dict[str, object] = {
        "doi": "10.1234/example",
        "title": "Digital behavior and wellbeing",
    }
    packet = build_packet(
        source=source,
        pages=pages,
        study_question="Does screen time affect wellbeing?",
        population="adolescents",
        design="cross_sectional",
        exposure_variable="screen_time",
        outcome_variable="anxiety",
        analysis_method="pearson correlation",
        finding_statement="Screen time correlates with anxiety.",
        origin="SOURCE_TEXT",
        effect_direction="positive",
        exact_quote="Screen time correlates with anxiety.",
        extraction_agent="measurement_scientist",
    )
    assert packet.evidence_level.value in {"A", "B"}
    assert len(packet.studies) == 1
    assert len(packet.studies[0].findings) == 1
