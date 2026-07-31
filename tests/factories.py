from typing import Any


def make_research_contract(**overrides: Any) -> Any:
    """Build a deterministic valid research contract for tests."""
    from packages.research.contracts import ResearchContract

    values: dict[str, Any] = {
        "question": "数字行为是否影响心理健康？",
        "scope": {
            "populations": ["adolescents"],
            "regions": ["global"],
            "languages": ["en", "zh"],
            "date_from": "2020-01-01",
            "date_until": "2025-12-31",
            "evidence_priorities": ["CAUSAL_OR_REVERSE_CAUSAL", "MEASUREMENT"],
            "allow_preprints": False,
        },
        "budget": {
            "wall_clock_minutes": 60,
            "model_cost_usd": "25.00",
            "tool_call_limit": 100,
            "source_limit": 50,
        },
        "user_evidence": {
            "dois": ["10.1000/example"],
            "bibtex_entries": [],
            "pdf_object_ids": [],
        },
    }
    values.update(overrides)
    return ResearchContract.model_validate(values)
