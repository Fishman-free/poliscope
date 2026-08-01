from __future__ import annotations

from uuid import UUID

from packages.evidence.contracts import EvidenceNodeType
from packages.kernel.contracts import ContractModel


class DiscriminatingStudy(ContractModel):
    id: UUID
    target_blindspot_ids: tuple[UUID, ...]
    objective: str
    recommended_design: str
    key_data: tuple[str, ...]
    competing_predictions: tuple[str, ...]
    resolvable_blindspots: tuple[UUID, ...]
    expected_information_gain: float
    node_type: EvidenceNodeType = EvidenceNodeType.DISCRIMINATING_STUDY
    artifact_type: str = "research_recommendation"

    def __init__(self, **data) -> None:
        super().__init__(**data)
        if len(self.competing_predictions) < 2:
            raise ValueError(
                "DiscriminatingStudy requires at least "
                "two competing predictions"
            )
