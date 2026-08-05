"""Best-effort DOI extraction from pasted BibTeX text."""

from __future__ import annotations

from packages.papers.bibtex import extract_dois_from_bibtex


def test_extracts_doi_from_field_value() -> None:
    entry = (
        "@article{vogel2024,\n"
        '  author = {Vogel, Erin A.},\n'
        "  doi = {10.1000/j.jadohealth.2024.01.001},\n"
        "}"
    )
    assert extract_dois_from_bibtex(entry) == (
        "10.1000/j.jadohealth.2024.01.001",
    )


def test_extracts_doi_with_quotes_and_braces() -> None:
    assert extract_dois_from_bibtex('doi = "10.1000/xyz"') == ("10.1000/xyz",)
    assert extract_dois_from_bibtex("doi = {10.1000/xyz},") == ("10.1000/xyz",)


def test_doi_field_without_trailing_comma_still_extracted() -> None:
    # Last field of an entry has no trailing comma; the field regex cannot
    # match it, so the anywhere-fallback must.
    entry = "@misc{m,\n  doi = {10.1000/xyz}\n}"
    assert extract_dois_from_bibtex(entry) == ("10.1000/xyz",)


def test_normalizes_and_dedupes_dois() -> None:
    entry = (
        "@article{a, doi = {10.1000/ABC}}\n"
        '@article{b, doi = "https://doi.org/10.1000/abc"}'
    )
    assert extract_dois_from_bibtex(entry) == ("10.1000/abc",)


def test_empty_and_garbage_entries_yield_nothing() -> None:
    assert extract_dois_from_bibtex("") == ()
    assert extract_dois_from_bibtex("@article{garbage, title = {no doi here}}") == ()
    assert extract_dois_from_bibtex("not bibtex at all") == ()


def test_multiple_entries_keep_first_seen_order() -> None:
    entry = (
        "@article{b, doi = {10.1000/b}}\n"
        "@article{a, doi = {10.1000/a}}\n"
        "@article{c, doi = {10.1000/a}}"
    )
    assert extract_dois_from_bibtex(entry) == ("10.1000/b", "10.1000/a")
