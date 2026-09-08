from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID


@dataclass(frozen=True, slots=True)
class JointModelInput:
    claim_refs: tuple[UUID, ...]
    challenge_refs: tuple[UUID, ...]
    strongest_opposition_refs: tuple[UUID, ...]
    falsification_conditions: tuple[str, ...]
    boundary_conditions: tuple[str, ...]
    unresolved_conflicts: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class JointModelOutput:
    ready: bool
    missing_fields: tuple[str, ...] = ()
    conditional_consensus: str = ""
    supporting_refs: tuple[UUID, ...] = ()
    opposing_refs: tuple[UUID, ...] = ()
    hinge_variables: tuple[str, ...] = ()
    boundary_conditions: tuple[str, ...] = ()
    unresolved_conflicts: tuple[str, ...] = ()
    falsification_conditions: tuple[str, ...] = ()


_REQUIRED_FIELDS = (
    "strongest_opposition_refs",
    "falsification_conditions",
)


@dataclass
class JointModelingHandler:
    _last_output: JointModelOutput | None = field(default=None, init=False)

    def run(self, input: JointModelInput) -> JointModelOutput:
        missing = tuple(
            field_name
            for field_name in _REQUIRED_FIELDS
            if not getattr(input, field_name)
        )
        if missing:
            output = JointModelOutput(ready=False, missing_fields=missing)
            self._last_output = output
            return output
        hinge_variables = tuple(
            f"hinge-{i}" for i in range(len(input.claim_refs))
        )
        # Reader-facing Chinese fallback (quoted verbatim in the final paper,
        # so English scaffolding here leaked as system noise to readers).
        consensus_parts = [f"综合结论以 {len(input.claim_refs)} 项已有主张为前提"]
        if input.boundary_conditions:
            consensus_parts.append(
                f"其适用范围受 {len(input.boundary_conditions)} 项边界条件约束"
            )
        if input.unresolved_conflicts:
            consensus_parts.append(
                f"目前仍有 {len(input.unresolved_conflicts)} 处分歧尚未解决"
            )
        output = JointModelOutput(
            ready=True,
            missing_fields=(),
            conditional_consensus="；".join(consensus_parts),
            supporting_refs=input.claim_refs,
            opposing_refs=input.strongest_opposition_refs,
            hinge_variables=hinge_variables,
            boundary_conditions=input.boundary_conditions,
            unresolved_conflicts=input.unresolved_conflicts,
            falsification_conditions=input.falsification_conditions,
        )
        self._last_output = output
        return output
