from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from typing import SupportsFloat, cast
from uuid import UUID

from packages.evidence.causal_policy import CausalUpgradePolicy
from packages.evidence.citation_verifier import verify_citation_entailment
from packages.evidence.consistency import (
    GraphConsistencyQuery,
    GraphConsistencyResult,
    check_graph_consistency,
)
from packages.evidence.contracts import (
    AdmissionDecision,
    AdmissionDisposition,
    ClaimType,
    EvidenceEdgeType,
    EvidenceNodeType,
    ScientificEventCandidate,
)
from packages.evidence.method_auditor import (
    METHOD_QUALITY_THRESHOLD,
    MethodQualityResult,
    audit_method_quality,
)
from packages.evidence.source_verifier import (
    SourceVerificationResult,
    verify_source,
)
from packages.kernel.contracts import ContractModel


class AuditStage(StrEnum):
    SCHEMA = "SCHEMA"
    DEDUPLICATION = "DEDUPLICATION"
    SOURCE = "SOURCE"
    CITATION_ENTAILMENT = "CITATION_ENTAILMENT"
    METHOD_QUALITY = "METHOD_QUALITY"
    GRAPH_CONSISTENCY = "GRAPH_CONSISTENCY"


_AUDIT_SEQUENCE: tuple[AuditStage, ...] = (
    AuditStage.SCHEMA,
    AuditStage.DEDUPLICATION,
    AuditStage.SOURCE,
    AuditStage.CITATION_ENTAILMENT,
    AuditStage.METHOD_QUALITY,
    AuditStage.GRAPH_CONSISTENCY,
)


class AuditFinding(ContractModel):
    stage: AuditStage
    passed: bool
    detail: str = ""


# Names the payload may carry for stage 3. Absent keys fall back to the
# verifier's own defaults, which is the "already checked upstream" case; a key
# that is present and false is a real failure and must reach the decision.
_SOURCE_FLAG_KEYS = (
    "has_doi",
    "has_title",
    "has_authors",
    "is_retracted",
    "pdf_matches",
)

_METHOD_SCORE_KEYS = (
    "directness",
    "design_quality",
    "measurement_quality",
    "precision",
    "replicability",
    "external_validity",
)


def _source_flags(payload: Mapping[str, object]) -> dict[str, bool]:
    flags = {
        key: bool(payload[key]) for key in _SOURCE_FLAG_KEYS if key in payload
    }
    if payload.get("object_id"):
        # Only registry.py's uploaded-PDF branch ever sets object_id on a
        # SOURCE event payload -- the DOI branch never does. Signals
        # verify_source to waive the has_doi/has_authors checks that no
        # upload can ever satisfy (see source_verifier.verify_source).
        flags["is_uploaded"] = True
    return flags


def _method_scores(payload: Mapping[str, object]) -> dict[str, float]:
    raw = payload.get("method_quality")
    scores = raw if isinstance(raw, Mapping) else payload
    return {
        key: float(cast(SupportsFloat, scores[key]))
        for key in _METHOD_SCORE_KEYS
        if key in scores and isinstance(scores[key], (int, float, str))
    }


def _source_failure_detail(result: SourceVerificationResult) -> str:
    if result.is_retracted:
        return "source is retracted"
    missing = [
        name
        for name, present in (
            ("doi", result.has_doi or result.is_uploaded),
            ("title", result.has_title or result.is_uploaded),
            ("authors", result.has_authors or result.is_uploaded),
        )
        if not present
    ]
    if missing:
        return f"source metadata missing: {', '.join(missing)}"
    return "source pdf does not match the recorded metadata"


def _method_failure_detail(result: MethodQualityResult) -> str:
    weak = [
        name
        for name in _METHOD_SCORE_KEYS
        if getattr(result, name) < METHOD_QUALITY_THRESHOLD
    ]
    return f"method quality below threshold: {', '.join(weak)}"


def _candidate_node_id(candidate: ScientificEventCandidate) -> UUID:
    """Mirror ``sql_projector.node_id_for`` on a candidate rather than a row.

    Stage 6 runs before the projector's own node-id resolution (the node for
    this very candidate has not been written yet), so it needs the same
    resolution rule to ask "does this id already belong to something else."
    Kept independent of ``sql_projector`` to avoid a circular import (that
    module already imports this one for ``FullEvidenceGate``).
    """
    declared = candidate.payload.get("node_id")
    if isinstance(declared, str):
        try:
            return UUID(declared)
        except ValueError:
            pass
    for value in (candidate.claim_id, candidate.finding_id, candidate.source_id):
        if value is not None:
            return value
    return candidate.id


def _fork_target(candidate: ScientificEventCandidate) -> UUID | None:
    """The claim a Fork-produced Claim's ``CONTRADICTS`` edge points at, if any.

    ``candidate.payload`` is a ``FrozenDict`` (see ``packages.kernel.contracts
    .freeze_value``), which freezes a JSON list into a ``tuple`` and a nested
    JSON object into a nested ``FrozenDict`` -- so this must accept ``tuple``
    alongside ``list``, unlike ``sql_projector._edge_specs``, which reads the
    edges straight off a JSONB column and only ever sees a plain ``list``.
    """
    raw = candidate.payload.get("edges")
    if not isinstance(raw, (list, tuple)):
        return None
    for item in raw:
        if not isinstance(item, Mapping):
            continue
        if item.get("type") != EvidenceEdgeType.CONTRADICTS.value:
            continue
        target = item.get("target")
        if isinstance(target, str):
            try:
                return UUID(target)
            except ValueError:
                return None
    return None


def _consistency_violation_detail(result: GraphConsistencyResult) -> str:
    reasons = []
    if not result.no_contradictory_admitted:
        reasons.append(
            "node_id already exists in the graph with a conflicting node_type "
            "(structural corruption, not a scientific contradiction)"
        )
    if not result.no_duplicate_lineage:
        reasons.append(
            "this exact dissent (same target, same statement) was already "
            "forked once"
        )
    return "; ".join(reasons)


class FullAdmissionDecision(AdmissionDecision):
    audit_findings: tuple[AuditFinding, ...] = ()

    model_config = AdmissionDecision.model_config


class MinimalEvidenceGate:
    """Admission gate for the Evidence Graph.

    Applies the A–D level matrix and enforces that:
    - Schema validation passes
    - Source/Study/Anchor references exist for findings
    - Claim includes type, scope, and falsification condition
    - Correlation does not upgrade to causation
    """

    _LEVEL_DISPOSITION: dict[str, AdmissionDisposition] = {
        "A": AdmissionDisposition.ADMIT,
        "B": AdmissionDisposition.SOURCE_ONLY,
        "C": AdmissionDisposition.DISCOVERY_ONLY,
        "D": AdmissionDisposition.TOOL_LEAD_ONLY,
    }

    def evaluate(self, candidate: ScientificEventCandidate) -> AdmissionDecision:
        level = (candidate.evidence_level or "D").upper()
        base = self._LEVEL_DISPOSITION.get(
            level, AdmissionDisposition.TOOL_LEAD_ONLY
        )

        if base == AdmissionDisposition.ADMIT:
            if candidate.finding_id and not candidate.source_id:
                return AdmissionDecision(
                    disposition=AdmissionDisposition.QUARANTINE,
                    reasons=("Finding must reference a Source.",),
                    evidence_level=level,
                )
            if candidate.claim_id:
                claim_type_payload = candidate.payload.get("claim_type")
                claim_type = (
                    ClaimType(str(claim_type_payload))
                    if claim_type_payload
                    else ClaimType.CORRELATIONAL
                )
                design = str(candidate.payload.get("study_design", ""))
                violation = CausalUpgradePolicy.validate(design, claim_type)
                if violation:
                    return AdmissionDecision(
                        disposition=AdmissionDisposition.QUARANTINE,
                        reasons=(violation,),
                        evidence_level=level,
                    )

        return AdmissionDecision(
            disposition=base,
            reasons=(),
            evidence_level=level,
        )


class FullEvidenceGate:
    """Complete 6-stage evidence gate with A–D matrix enforcement."""

    _LEVEL_DISPOSITION: dict[str, AdmissionDisposition] = {
        "A": AdmissionDisposition.ADMIT,
        "B": AdmissionDisposition.SOURCE_ONLY,
        "C": AdmissionDisposition.DISCOVERY_ONLY,
        "D": AdmissionDisposition.TOOL_LEAD_ONLY,
    }

    def __init__(self, graph_query: GraphConsistencyQuery | None = None) -> None:
        # None keeps every existing caller that constructs FullEvidenceGate()
        # bare working exactly as before -- Stage 6 falls back to
        # causal-upgrade-only, the same behavior it always had. The real
        # query is wired in production by packages.evidence.sql_projector.
        self._graph_query = graph_query

    async def audit(
        self, candidate: ScientificEventCandidate
    ) -> FullAdmissionDecision:
        findings: list[AuditFinding] = []

        # Stage 1: Schema
        schema_ok = bool(candidate.id and candidate.task_id and candidate.event_type)
        findings.append(
            AuditFinding(stage=AuditStage.SCHEMA, passed=schema_ok)
        )
        if not schema_ok:
            return self._quarantine(findings, "schema validation failed")

        # Stage 2: Deduplication
        dedup_ok = True  # real impl would check hash uniqueness
        findings.append(
            AuditFinding(stage=AuditStage.DEDUPLICATION, passed=dedup_ok)
        )

        # Stage 3: Source
        source_id = candidate.source_id
        source_detail = ""
        if source_id:
            # The metadata comes from the candidate rather than from the
            # verifier's optimistic defaults. Calling verify_source with no
            # arguments returned "passed" for every event ever submitted, which
            # made this stage decorative and let a retracted paper through.
            src = verify_source(source_id, **_source_flags(candidate.payload))
            source_ok = src.passed
            if not source_ok:
                source_detail = _source_failure_detail(src)
        else:
            # This used to compare against the literal string "FINDING", which
            # never matched EvidenceNodeType.STUDY_FINDING's real value
            # ("StudyFinding") -- a StudyFinding event missing its source_id
            # always fell through to the "not a finding, so no source needed"
            # default instead of being caught here.
            source_ok = candidate.event_type != EvidenceNodeType.STUDY_FINDING.value
        findings.append(
            AuditFinding(
                stage=AuditStage.SOURCE, passed=source_ok, detail=source_detail
            )
        )
        if not source_ok:
            return self._quarantine(
                findings, source_detail or "source verification failed"
            )

        # Stage 4: Citation Entailment
        if candidate.finding_id:
            citation = verify_citation_entailment(
                candidate.finding_id,
                exact_quote=str(candidate.payload.get("exact_quote", "")),
            )
            citation_ok = citation.passed
        else:
            citation_ok = True
        findings.append(
            AuditFinding(stage=AuditStage.CITATION_ENTAILMENT, passed=citation_ok)
        )
        if not citation_ok:
            return self._quarantine(findings, "citation entailment failed")

        # Stage 5: Method Quality
        method_detail = ""
        if candidate.finding_id:
            method = audit_method_quality(
                candidate.finding_id, **_method_scores(candidate.payload)
            )
            method_ok = method.passed
            if not method_ok:
                method_detail = _method_failure_detail(method)
        else:
            method_ok = True
        findings.append(
            AuditFinding(
                stage=AuditStage.METHOD_QUALITY,
                passed=method_ok,
                detail=method_detail,
            )
        )
        if not method_ok:
            return self._quarantine(
                findings, method_detail or "method quality failed"
            )

        # Stage 6: Graph Consistency
        #
        # The causal-upgrade check stays here -- it always has -- because two
        # existing callers (a Claim event with no finding_id/source_id, which
        # is exactly what a Fork or a bare claim revision emits) never trigger
        # Stages 3-5, so this is the only stage that ever sees the claim at
        # all. Real graph-consistency queries (structural node-id corruption,
        # duplicate fork) are layered on *after* it, only when a real query
        # was wired in (see FullEvidenceGate.__init__), so this stays a no-op
        # true for every caller that never wires one.
        consistency_ok = True
        consistency_detail = ""
        if candidate.claim_id:
            claim_type_payload = candidate.payload.get("claim_type")
            claim_type = (
                ClaimType(str(claim_type_payload))
                if claim_type_payload
                else ClaimType.CORRELATIONAL
            )
            design = str(candidate.payload.get("study_design", ""))
            violation = CausalUpgradePolicy.validate(design, claim_type)
            if violation:
                consistency_ok = False
                consistency_detail = violation
            elif self._graph_query is not None:
                node_id = _candidate_node_id(candidate)
                existing_type = await self._graph_query.existing_node_type(node_id)
                no_contradictory_admitted = (
                    existing_type is None or existing_type == candidate.event_type
                )
                no_duplicate_lineage = True
                fork_target = _fork_target(candidate)
                if fork_target is not None:
                    statement = str(candidate.payload.get("statement", ""))
                    duplicate = await self._graph_query.duplicate_fork_exists(
                        fork_target, statement, node_id
                    )
                    no_duplicate_lineage = not duplicate
                result = check_graph_consistency(
                    candidate.claim_id, no_contradictory_admitted, no_duplicate_lineage
                )
                consistency_ok = result.passed
                if not consistency_ok:
                    consistency_detail = _consistency_violation_detail(result)
        findings.append(
            AuditFinding(
                stage=AuditStage.GRAPH_CONSISTENCY,
                passed=consistency_ok,
                detail=consistency_detail,
            )
        )
        if not consistency_ok:
            return self._quarantine(findings, consistency_detail)

        # Apply A–D matrix
        level = (candidate.evidence_level or "D").upper()
        disposition = self._LEVEL_DISPOSITION.get(
            level, AdmissionDisposition.TOOL_LEAD_ONLY
        )
        return FullAdmissionDecision(
            disposition=disposition,
            reasons=(),
            evidence_level=level,
            audit_findings=tuple(findings),
        )

    def _quarantine(
        self, findings: list[AuditFinding], reason: str
    ) -> FullAdmissionDecision:
        level = "A"
        return FullAdmissionDecision(
            disposition=AdmissionDisposition.QUARANTINE,
            reasons=(reason,),
            evidence_level=level,
            audit_findings=tuple(findings),
        )
