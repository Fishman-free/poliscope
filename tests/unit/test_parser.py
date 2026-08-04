"""Tests for ``detect_dataset_identifier``.

CLAUDE.md 7 forbids fabricating a dataset identifier the source text does not
actually contain, so this only covers deterministic pattern matches over
``PageText`` -- no model call involved.
"""

from __future__ import annotations

from packages.papers.parser import PageText, detect_dataset_identifier


def _pages(*texts: str) -> list[PageText]:
    return [PageText(page_number=i + 1, text=t) for i, t in enumerate(texts)]


def test_no_declaration_returns_none() -> None:
    pages = _pages("This paper has no data availability statement at all.")
    assert detect_dataset_identifier(pages) is None


def test_icpsr_accession_is_detected() -> None:
    pages = _pages(
        "Data Availability: data were obtained from ICPSR study number 37183."
    )
    assert detect_dataset_identifier(pages) == "ICPSR:37183"


def test_osf_slug_is_detected() -> None:
    pages = _pages("Materials and data are posted at osf.io/ab3xy for review.")
    assert detect_dataset_identifier(pages) == "OSF:ab3xy"


def test_dryad_doi_is_detected() -> None:
    pages = _pages(
        "Data are available at Dryad Digital Repository: 10.5061/dryad.q2z3x4v."
    )
    assert detect_dataset_identifier(pages) == "Dryad:10.5061/dryad.q2z3x4v"


def test_zenodo_record_url_is_detected() -> None:
    pages = _pages("Code and data archived at zenodo.org/record/1234567.")
    assert detect_dataset_identifier(pages) == "Zenodo:1234567"


def test_zenodo_doi_is_detected() -> None:
    pages = _pages("Archived under doi 10.5281/zenodo.7654321.")
    assert detect_dataset_identifier(pages) == "Zenodo:7654321"


def test_dataverse_doi_is_detected() -> None:
    pages = _pages(
        "Replication data available at doi:10.7910/DVN/ABCDEF (Harvard Dataverse)."
    )
    assert detect_dataset_identifier(pages) == "Dataverse:10.7910/DVN/ABCDEF"


def test_match_on_later_page_is_found() -> None:
    pages = _pages(
        "Introduction with no relevant statement here.",
        "Data Availability: osf.io/xy9z1",
    )
    assert detect_dataset_identifier(pages) == "OSF:xy9z1"


def test_trailing_sentence_punctuation_is_stripped() -> None:
    pages = _pages("See osf.io/ab12c.")
    assert detect_dataset_identifier(pages) == "OSF:ab12c"
