# Poliscope Testing

## MVP Acceptance Matrix

Each spec item maps to a precise test:

| # | Spec Item | Test Path |
|---|-----------|-----------|
| 1 | 7 seats participate | tests/unit/test_council_contracts.py |
| 2 | Atomic claims required | tests/integration/test_research_service.py |
| 3 | Evidence Gate blocks correlation→causation | tests/unit/test_minimal_evidence_gate.py |
| 4 | Full 6-stage audit | tests/integration/test_full_evidence_gate.py |
| 5 | Dialectical Fold preserves dissent | tests/unit/test_dialectical_fold.py |
| 6 | Independent evidence clusters | tests/integration/test_independent_clusters.py |
| 7 | Blindspot 5-dim scoring | tests/unit/test_blindspot_score.py |
| 8 | DiscriminatingStudy artifact | tests/unit/test_discriminating_study.py |
| 9 | Budget exhaustion ≠ saturation | tests/unit/test_evidence_saturation.py |
| 10 | Single seat degradation | tests/integration/test_single_seat_degradation.py |
| 11 | Causal golden cases | tests/golden/test_causal_entailment.py |
| 12 | Report safety | tests/unit/test_report_safety.py |
| 13 | CLI contract | tests/unit/test_cli_contract.py |
| 14 | Workspace DTO whitelist | tests/integration/test_workspace_api.py |
| 15 | SSE resume | tests/integration/test_sse_contract.py |
| 16 | Release gate | tests/e2e/test_release_gate.py |
