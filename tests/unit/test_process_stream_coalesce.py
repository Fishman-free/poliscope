"""Unit tests for ProcessStreamWriter's in-batch coalescing (no database)."""

from __future__ import annotations

from packages.evidence.process_stream import _coalesce_batch


def test_adjacent_same_slice_token_deltas_concatenate() -> None:
    pending = [
        ("model_token", {"text": "a", "seat": "s1", "phase": "P"}),
        ("model_token", {"text": "b", "seat": "s1", "phase": "P"}),
        ("model_token", {"text": "c", "seat": "s1", "phase": "P"}),
    ]

    merged = _coalesce_batch(pending)

    assert len(merged) == 1
    kind, payload = merged[0]
    assert kind == "model_token"
    assert payload["text"] == "abc"


def test_deltas_from_different_seats_stay_separate() -> None:
    pending = [
        ("model_token", {"text": "a", "seat": "s1", "phase": "P"}),
        ("model_token", {"text": "b", "seat": "s2", "phase": "P"}),
    ]

    merged = _coalesce_batch(pending)

    assert len(merged) == 2
    assert merged[0][1]["text"] == "a"
    assert merged[1][1]["text"] == "b"


def test_structural_rows_break_and_survive() -> None:
    pending = [
        ("model_token", {"text": "a", "seat": "s1", "phase": "P"}),
        ("seat_deliberation", {"seat": "s1", "phase": "Q"}),
        ("model_token", {"text": "b", "seat": "s1", "phase": "Q"}),
        ("model_done", {"seat": "s1", "phase": "Q"}),
    ]

    merged = _coalesce_batch(pending)

    assert [kind for kind, _ in merged] == [
        "model_token",
        "seat_deliberation",
        "model_token",
        "model_done",
    ]


def test_adjacent_heartbeats_keep_only_the_newest() -> None:
    pending = [
        ("seat_working", {"seat": "s1", "phase": "P", "elapsed": 1}),
        ("seat_working", {"seat": "s1", "phase": "P", "elapsed": 2}),
        ("seat_working", {"seat": "s1", "phase": "P", "elapsed": 3}),
    ]

    merged = _coalesce_batch(pending)

    assert len(merged) == 1
    assert merged[0][1]["elapsed"] == 3


def test_coalescing_does_not_mutate_input() -> None:
    # A failed flush puts the original batch back; mutated/copied payloads
    # would be double-merged on retry.
    pending = [
        ("model_token", {"text": "a", "seat": "s1", "phase": "P"}),
        ("model_token", {"text": "b", "seat": "s1", "phase": "P"}),
    ]

    _coalesce_batch(pending)

    assert pending[0][1]["text"] == "a"
    assert pending[1][1]["text"] == "b"
    merged_again = _coalesce_batch(pending)
    assert merged_again[0][1]["text"] == "ab"
