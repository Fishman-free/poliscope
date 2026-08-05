"""One end-to-end ForesightBlindspot demo case: FULL_POLISCOPE, scripted top to bottom.

Every other evaluation test either exercises one scoring function in isolation
(``test_evaluation_scoring.py``) or one seam of the harness (``test_evaluation_
harness.py``). This file is the one the plan's Phase 6 asks for on top of
those: a single run of :func:`run_baseline` against
``BaselineVariant.FULL_POLISCOPE``, driven by a scripted
:class:`~packages.models.contracts.ModelGateway`, a scripted
:class:`~packages.council.rounds.registry.SourceAcquirer`, and a scripted
:class:`~packages.council.rounds.registry.FindingExtractor` -- the same
test-double pattern already proven in ``tests/unit/test_run_acquisition_
finding_extraction.py`` and ``scripts/seed_demo_task.py`` -- so that the
Blindspot Recall/Precision, Citation Entailment, and Evidence Independence
scores are computed from a real (if scripted) council run rather than from
hand-built :class:`LedgerEntry` fixtures.

**Causal Overclaim is the one score this case cannot produce, honestly.**
``score_causal_overclaim`` reads ``study_design`` off a ``Claim`` event's
payload (see ``packages/evidence/gate.py``'s Stage 6 and ``packages/
evaluation/scoring.py``). The only place that ever emits a ``Claim`` event is
the Fork path in ``packages.council.rounds.registry._fork_events``, and since
Phase 4 it no longer hardcodes ``claim_type="correlational"`` -- a seat that
self-reports ``claim_type``/``study_design`` in its ``fork`` mapping (see that
function's docstring on why self-reporting, not a classifier, is the honest
option here) can produce a genuine causal claim. This demo case's
``_DemoGateway`` simply never answers ``CROSS_EXAMINATION`` with any
challenge at all, let alone a fatal one with a ``fork``, so no ``Claim`` event
of any kind is emitted here -- a scenario-specific gap (this scripted run
does not exercise that phase), not the system-wide impossibility it used to
be. This test asserts ``score_causal_overclaim`` returns ``None`` here and
says why, rather than manufacturing a Claim event this particular scripted
run does not produce (CLAUDE.md 7: the system must admit what it does not
know, including about its own evaluation harness). The Fork-produced causal
path itself is covered directly in ``tests/unit/
test_run_cross_examination_fork.py``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from uuid import UUID, uuid4

from packages.council.contracts import Seat
from packages.epistemo.contracts import TaskPhase
from packages.evaluation.harness import BaselineVariant, run_baseline
from packages.evaluation.scoring import (
    score_blindspots,
    score_causal_overclaim,
    score_citation_entailment,
    score_dissent_preservation,
    score_evidence_independence,
)
from packages.evidence.contracts import EvidenceNodeType
from packages.kernel.contracts import FrozenDict
from packages.models.contracts import ModelRequest, ModelResult, SchemaStatus

_QUESTION = "does reducing adolescent screen time lower depressive symptoms?"

_DOIS = ("10.1234/cohort-a", "10.1234/cohort-b", "10.1234/rct-c")

# Two sources share a dataset -- the whole reason evidence independence is
# below 1.0 in this run rather than trivially perfect.
_DATASET_IDS = {
    _DOIS[0]: "adolescent-cohort-2021",
    _DOIS[1]: "adolescent-cohort-2021",
    _DOIS[2]: None,
}

_BLINDSPOTS = (
    ("screen time relies on self-report, a clear measurement bias", "0.8", "0.7"),
    (
        "nearly every sample is from a western high income country, "
        "external validity untested",
        "0.7",
        "0.6",
    ),
)


@dataclass(frozen=True, slots=True)
class _Acquired:
    source_id: UUID
    doi: str
    title: str
    evidence_level: str
    already_known: bool = False
    authors: tuple[str, ...] = ()
    dataset_id: str | None = None
    object_id: UUID | None = None
    document_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class _Refused:
    query: str
    reason: str


@dataclass(frozen=True, slots=True)
class _AcquisitionResult:
    acquired: tuple[_Acquired, ...] = ()
    refused: tuple[_Refused, ...] = ()
    unresolvable: tuple[str, ...] = ()


class _DemoAcquirer:
    """Ignores what was actually requested and returns the same three sources.

    Matches the precedent in ``test_run_acquisition_finding_extraction.py``'s
    ``_FakeAcquirer``: a scripted double answers with a fixed, known-shape
    result regardless of the request list's exact contents, since what is
    under test here is what the round and the scoring functions do with an
    acquisition result, not a real retrieval query language.
    """

    def __init__(self) -> None:
        self.source_ids = {doi: uuid4() for doi in _DOIS}

    async def acquire(self, requests: list[tuple[Seat, str]]) -> _AcquisitionResult:
        return _AcquisitionResult(
            acquired=tuple(
                _Acquired(
                    source_id=self.source_ids[doi],
                    doi=doi,
                    title=f"A study at {doi}",
                    evidence_level="B",
                    authors=("A. Researcher",),
                    dataset_id=_DATASET_IDS[doi],
                )
                for doi in _DOIS
            )
        )

    async def acquire_uploaded(
        self, object_ids: tuple[UUID, ...]
    ) -> _AcquisitionResult:
        # Not exercised by this file's scenario -- see module docstring on
        # what this demo case does and does not exercise.
        return _AcquisitionResult()

    async def acquire_dois(self, dois: tuple[str, ...]) -> _AcquisitionResult:
        return _AcquisitionResult()

    async def acquire_knowledge_documents(
        self, documents: tuple[object, ...]
    ) -> _AcquisitionResult:
        return _AcquisitionResult()


@dataclass(frozen=True, slots=True)
class _Extraction:
    ok: bool
    reason: str = ""
    finding_id: UUID | None = None
    evidence_level: str = ""
    exact_quote: str = ""
    finding_statement: str = ""
    method_quality: dict[str, float] = field(default_factory=dict)


class _DemoFindingExtractor:
    """One clean extraction, one quote-less "ok" extraction, one outright failure.

    This mix is deliberate, not arbitrary: it is what makes
    ``score_citation_entailment`` land on a real fraction (1 of 2 emitted
    StudyFinding events has a usable quote) instead of a vacuous 0.0 or 1.0
    that a uniform fixture would produce.
    """

    def __init__(self) -> None:
        self._by_doi = {
            _DOIS[0]: _Extraction(
                ok=True,
                finding_id=uuid4(),
                evidence_level="A",
                exact_quote=(
                    "a preregistered cohort found a significant longitudinal "
                    "association after adjustment for baseline symptoms"
                ),
                finding_statement=(
                    "Screen time correlates with later depressive symptoms."
                ),
                method_quality={"directness": 0.7},
            ),
            _DOIS[1]: _Extraction(
                ok=True,
                finding_id=uuid4(),
                evidence_level="B",
                exact_quote="",
                finding_statement="A related cohort reports a similar pattern.",
                method_quality={"directness": 0.4},
            ),
            _DOIS[2]: _Extraction(ok=False, reason="no open access full text url"),
        }

    async def extract(self, source_id: UUID, doi: str) -> _Extraction:
        return self._by_doi[doi]

    async def extract_uploaded(
        self, source_id: UUID, object_id: UUID
    ) -> _Extraction:
        # Not exercised by this file's scenario -- see acquire_uploaded above.
        raise NotImplementedError

    async def extract_knowledge_document(
        self, source_id: UUID, document_id: UUID
    ) -> _Extraction:
        # Not exercised by this file's scenario -- see acquire_uploaded above.
        raise NotImplementedError


class _DemoGateway:
    """Answers every phase this demo case needs; every other phase gets {}.

    Mirrors ``scripts/seed_demo_task.py``'s ``_Gateway`` -- the same scripted
    stand-in already proven against the real worker pipeline -- adapted to the
    smaller set of phases this case actually exercises. The ADVERSARY_FALSIFIER
    seat's FINAL_REJUDGMENT answer contains "反对", which
    ``packages.council.rounds.final_rejudgment._detect_dissent`` matches, so
    this run also produces one genuine dissent with a matching
    DissentCertificate (score_dissent_preservation's non-trivial path, not the
    "nobody dissented" default).
    """

    async def invoke(self, request: ModelRequest) -> ModelResult:
        phase = TaskPhase(request.purpose)
        seat = request.actor
        payload: dict[str, object] = {}
        if phase is TaskPhase.PRECOMMITMENT:
            payload = {
                "initial_judgment": f"{seat}: correlational evidence only so far.",
                "confidence": 0.4,
                "update_condition": "a preregistered RCT with adequate power.",
            }
        elif phase is TaskPhase.ACQUISITION:
            payload = {"requests": [f"doi {doi}" for doi in _DOIS]}
        elif (
            phase is TaskPhase.BLINDSPOT_BOUNTY
            and seat == Seat.ADVERSARY_FALSIFIER.value
        ):
            payload = {
                "blindspots": [
                    {
                        "id": str(uuid4()),
                        "statement": statement,
                        "impact": impact,
                        "uncertainty": uncertainty,
                        "investigability": "0.6",
                        "novelty": "0.5",
                        "normalized_cost": "0.3",
                    }
                    for statement, impact, uncertainty in _BLINDSPOTS
                ]
            }
        elif phase is TaskPhase.JOINT_MODELING:
            payload = {
                "boundary_conditions": [
                    "limited to adolescent samples in high-income countries."
                ],
                "unresolved_conflicts": [
                    "effect direction differs by gender in two cohorts."
                ],
            }
        elif phase is TaskPhase.FINAL_REJUDGMENT:
            if seat == Seat.ADVERSARY_FALSIFIER.value:
                payload = {
                    "final_judgment": (
                        f"{seat}: 我反对当前结论，测量偏差足以解释全部效应，保留异议。"
                    )
                }
            else:
                payload = {
                    "final_judgment": f"{seat}: narrows scope, does not withdraw."
                }
        return ModelResult(
            call_id=uuid4(),
            payload=FrozenDict(payload),
            input_tokens=50,
            output_tokens=50,
            cost_usd=Decimal("0"),
            latency_ms=5,
            retries=0,
            schema_status=SchemaStatus.OK,
        )


async def test_full_poliscope_demo_case_produces_real_scores() -> None:
    outcome = await run_baseline(
        BaselineVariant.FULL_POLISCOPE,
        _QUESTION,
        _DemoGateway(),
        acquirer=_DemoAcquirer(),
        finding_extractor=_DemoFindingExtractor(),
        confirmed_claims=(uuid4(),),
    )

    sources = [
        e for e in outcome.events if e.event_type == EvidenceNodeType.SOURCE.value
    ]
    findings = [
        e
        for e in outcome.events
        if e.event_type == EvidenceNodeType.STUDY_FINDING.value
    ]
    blindspots = [
        e for e in outcome.events if e.event_type == EvidenceNodeType.BLINDSPOT.value
    ]
    assert len(sources) == 3
    assert len(findings) == 2  # the third source's extraction failed outright

    recall, precision = score_blindspots(
        outcome.events, ("measurement_bias", "external_validity")
    )
    assert recall == 1.0
    # A third blindspot -- the source-diversity check's own "single source"
    # flag -- also fires here, since two of the three acquired sources share
    # a dataset_id (see run_acquisition's check_diversity/SourceDiversityInput
    # call). It does not match either expected keyword, which is exactly what
    # keeps precision below 1.0 rather than a fixture artifact.
    assert precision == 2 / 3
    assert len(blindspots) == 3

    entailment = score_citation_entailment(outcome.events)
    assert entailment == 0.5

    independence = score_evidence_independence(outcome.events)
    # Two of three admitted sources share one dataset -> two clusters, three papers.
    assert independence == 2 / 3

    dissent = score_dissent_preservation(outcome.events)
    # The adversarial falsifier's dissent is expected to survive as a
    # DissentCertificate rather than being silently dropped (CLAUDE.md 4).
    assert dissent == 1.0

    # Documented gap, not a bug: see the module docstring. This scripted
    # gateway never answers CROSS_EXAMINATION with a fatal fork, so no Claim
    # event is emitted in this run at all -- a scenario-specific gap, not
    # proof that no run ever could (see test_run_cross_examination_fork.py
    # for the Fork path that now can, since Phase 4).
    assert score_causal_overclaim(outcome.events) is None
