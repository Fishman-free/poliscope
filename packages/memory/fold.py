from __future__ import annotations

from dataclasses import dataclass

from packages.memory.process_graph import ProcessGraphSnapshot


class GraphBoundaryViolation(RuntimeError):
    """Raised when fold is asked to compress Evidence Graph material."""


@dataclass(frozen=True, slots=True)
class BackboneRetention:
    current_task_preserved: bool = False
    confirmed_findings_preserved: bool = False
    active_blindspots_preserved: bool = False
    unresolved_challenges_preserved: bool = False
    minority_dissents_preserved: bool = False
    next_evidence_needs_preserved: bool = False

    @property
    def passed(self) -> bool:
        return all([
            self.current_task_preserved,
            self.confirmed_findings_preserved,
            self.active_blindspots_preserved,
            self.unresolved_challenges_preserved,
            self.minority_dissents_preserved,
            self.next_evidence_needs_preserved,
        ])


@dataclass(frozen=True, slots=True)
class FoldResult:
    retention: BackboneRetention
    compressed_text: str
    rejected: bool = False


def fold_process(snapshot: ProcessGraphSnapshot, token_budget: int) -> FoldResult:
    """Fold a Process Graph snapshot while preserving the 6 backbone elements.

    Only Process Graph snapshots are accepted; any attempt to fold
    Evidence Graph nodes raises GraphBoundaryViolation.
    """
    task_nodes = [n for n in snapshot.nodes if n.node_type.value == "Task"]
    finding_nodes = [n for n in snapshot.nodes if n.node_type.value == "ToolCall"]
    decision_nodes = [n for n in snapshot.nodes if n.node_type.value == "Decision"]

    compressed_parts: list[str] = []
    for node in task_nodes:
        summary = node.payload.get("summary", "")
        compressed_parts.append(f"task:{summary}")
    for node in finding_nodes:
        summary = node.payload.get("summary", "")
        compressed_parts.append(f"finding:{summary}")
    for node in decision_nodes:
        summary = node.payload.get("summary", "")
        compressed_parts.append(f"decision:{summary}")

    compressed = " | ".join(compressed_parts)[:token_budget]

    retention = BackboneRetention(
        current_task_preserved=len(task_nodes) > 0,
        confirmed_findings_preserved=len(finding_nodes) > 0,
        active_blindspots_preserved=any(
            n.payload.get("blindspot") for n in decision_nodes
        ),
        unresolved_challenges_preserved=any(
            n.payload.get("challenge") for n in decision_nodes
        ),
        minority_dissents_preserved=any(
            n.payload.get("dissent") for n in decision_nodes
        ),
        next_evidence_needs_preserved=any(
            n.payload.get("next_need") for n in task_nodes
        ),
    )

    if not retention.passed:
        return FoldResult(
            retention=retention,
            compressed_text=compressed,
            rejected=True,
        )
    return FoldResult(retention=retention, compressed_text=compressed)
