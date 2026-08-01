from __future__ import annotations


def load_acceptance_matrix() -> dict:
    return {
        "spec_items": list(range(1, 17)),
        "items": [
            {"id": i, "test_path": f"tests/test_acceptance_{i}.py"}
            for i in range(1, 17)
        ],
    }
