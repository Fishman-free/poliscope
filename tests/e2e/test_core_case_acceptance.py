from __future__ import annotations

from packages.evaluation.annotation import load_acceptance_matrix


def test_release_matrix_maps_every_spec_acceptance_item() -> None:
    matrix = load_acceptance_matrix()
    assert set(matrix["spec_items"]) == set(range(1, 17))
    for item in matrix["items"]:
        assert "test_path" in item


def test_suite() -> None:
    test_release_matrix_maps_every_spec_acceptance_item()
