from __future__ import annotations

from packages.kernel.contracts import ContractModel


class EvalCase(ContractModel):
    case_id: str
    question: str
    expected_blindspots: tuple[str, ...]
    closed_corpus_date: str


class EvalResult(ContractModel):
    case_id: str
    blindspot_recall: float
    blindspot_precision: float
    citation_entailment: float
    evidence_independence: float | None = None
    causal_over_inference: float
    dissent_preservation: float
    false_consensus: float
    drift: float
    cost_per_blindspot: float | None = None
