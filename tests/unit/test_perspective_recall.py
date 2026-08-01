from __future__ import annotations

from packages.memory.projection import CAUSAL_POLICY, MEASUREMENT_POLICY
from packages.memory.recall import perspective_recall


def test_role_projections_differ() -> None:
    evidence = [
        {"type": "experimental", "finding": "e1"},
        {"type": "measurement", "finding": "m1"},
        {"type": "causal", "finding": "c1"},
    ]
    process_recall = "public-process"
    causal = perspective_recall(CAUSAL_POLICY, process_recall, evidence)
    measurement = perspective_recall(MEASUREMENT_POLICY, process_recall, evidence)
    assert causal.evidence_projection.items[0]["type"] == "experimental"
    assert measurement.evidence_projection.items[0]["type"] == "measurement"
    # process recall is passed through unchanged (no leak because it's public)
    assert causal.process_recall == process_recall
