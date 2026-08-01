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
        consensus_parts = [f"conditional on {len(input.claim_refs)} claims"]
        if input.boundary_conditions:
            consensus_parts.append(
                f"bounded by {len(input.boundary_conditions)} conditions"
            )
        if input.unresolved_conflicts:
            consensus_parts.append(
                f"{len(input.unresolved_conflicts)} unresolved conflicts noted"
            )
        output = JointModelOutput(
            ready=True,
            missing_fields=(),
            conditional_consensus="; ".join(consensus_parts),
            supporting_refs=input.claim_refs,
            opposing_refs=input.strongest_opposition_refs,
            hinge_variables=hinge_variables,
            boundary_conditions=input.boundary_conditions,
            unresolved_conflicts=input.unresolved_conflicts,
            falsification_conditions=input.falsification_conditions,
        )
        self._last_output = output
        return output
