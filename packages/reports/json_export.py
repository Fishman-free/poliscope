from __future__ import annotations

import json
from uuid import UUID


def render_json(
    task_id: UUID,
    question: str,
    atomic_claims: list[str],
    admitted_findings: list[dict],
    blindspots: list[dict],
    dissents: list[dict],
) -> str:
    return json.dumps(
        {
            "task_id": str(task_id),
            "question": question,
            "atomic_claims": atomic_claims,
            "admitted_findings": admitted_findings,
            "blindspots": blindspots,
            "dissents": dissents,
        },
        ensure_ascii=False,
        indent=2,
    )
