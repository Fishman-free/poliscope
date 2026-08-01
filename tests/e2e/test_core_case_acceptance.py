from __future__ import annotations

from packages.evaluation.annotation import load_acceptance_matrix


def test_release_matrix_maps_every_spec_acceptance_item() -> None:
    matrix = load_acceptance_matrix()
    spec_items = matrix["spec_items"]
    items = matrix["items"]
    assert isinstance(spec_items, list)
    assert isinstance(items, list)
    assert set(spec_items) == set(range(1, 17))
    for item in items:
        assert "test_path" in item
