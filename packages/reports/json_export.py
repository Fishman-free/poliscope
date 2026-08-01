"""JSON rendering for the Research Brief.

The same brief the markdown renders, in the shape the interface consumes. It
carries the limitations and the refused submissions too: a machine-readable
export that dropped them would let a downstream consumer present a complete
looking result over incomplete evidence, which is what CLAUDE.md 10 forbids.
"""

from __future__ import annotations

import json

from packages.reports.safety import sanitize_export
from packages.reports.service import BriefNode, ResearchBrief


def _node(node: BriefNode) -> dict[str, object]:
    return {
        "id": str(node.node_id),
        "node_type": node.node_type,
        "status": node.status,
        "payload": node.payload,
    }


def to_dict(brief: ResearchBrief) -> dict[str, object]:
    return {
        "task_id": str(brief.task_id),
        "question": brief.question,
        "status": brief.status,
        "confirmed_claims": [
            {
                "claim_id": str(claim.claim_id),
                "statement": claim.statement,
                "claim_type": claim.claim_type,
                "falsification_condition": claim.falsification_condition,
            }
            for claim in brief.confirmed_claims
        ],
        "findings": [_node(node) for node in brief.findings],
        "blindspots": [_node(node) for node in brief.blindspots],
        "dissents": [_node(node) for node in brief.dissents],
        "discriminating_studies": [
            _node(node) for node in brief.discriminating_studies
        ],
        "refuted_or_withdrawn": [_node(node) for node in brief.refuted_or_withdrawn],
        "limitations": list(brief.limitations),
        "unadmitted_events": list(brief.unadmitted_events),
        "absent_seats": sorted(set(brief.absent_seats)),
        "failed_phases": sorted(set(brief.failed_phases)),
        "skipped_phases": sorted(set(brief.skipped_phases)),
        "paper_count": brief.paper_count,
        "independent_cluster_count": brief.independent_cluster_count,
        "has_gaps": brief.has_gaps,
        "is_mental_health": brief.is_mental_health,
    }


def render_json(brief: ResearchBrief) -> str:
    return sanitize_export(
        json.dumps(to_dict(brief), ensure_ascii=False, indent=2)
    )
