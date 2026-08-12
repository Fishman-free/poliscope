"""The ForesightBlindspot demo case, as a first-class evaluation asset.

Raised from ``tests/unit/test_evaluation_demo_case.py`` so that the same
scripted run is usable both by the test (which asserts the exact numbers) and
by ``scripts/arbor_eval.py`` (which drives the Arbor evaluation loop). The
test file now imports these fixtures; behaviour is unchanged.

The scenario: does reducing adolescent screen time lower depressive symptoms?
Three DOIs are acquired -- two share one dataset (the whole reason evidence
independence lands below 1.0), one extraction fails outright, one extraction
produces a usable quote and one does not, every seat nominates its own
specialist blindspot in BLINDSPOT_BOUNTY (two from the adversarial falsifier,
one of which deliberately repeats the boundary scientist's nomination), and
the falsifier submits a genuine dissent. No database, no network, no model
calls: ``DemoGateway`` answers every phase deterministically, so a baseline
run is fast and reproducible.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from uuid import UUID, uuid4

from packages.council.contracts import Seat
from packages.council.rounds.registry import FindingExtractor, SourceAcquirer
from packages.epistemo.contracts import TaskPhase
from packages.kernel.contracts import FrozenDict
from packages.models.contracts import ModelRequest, ModelResult, SchemaStatus

QUESTION = "does reducing adolescent screen time lower depressive symptoms?"

DOIS = ("10.1234/cohort-a", "10.1234/cohort-b", "10.1234/rct-c")

# Two sources share a dataset -- the whole reason evidence independence is
# below 1.0 in this run rather than trivially perfect.
DATASET_IDS = {
    DOIS[0]: "adolescent-cohort-2021",
    DOIS[1]: "adolescent-cohort-2021",
    DOIS[2]: None,
}

# Gold blindspots for this case: one keyword per specialist seat, so the
# multi-seat bounty is fully covered (coarse keyword proxies for the human
# annotation that does not exist yet -- see packages/evaluation/scoring.py).
BLINDSPOT_KEYWORDS = (
    "mechanism",
    "reverse_causation",
    "measurement_bias",
    "replication",
    "external_validity",
    "publication_bias",
    "provenance",
)

# One specialist blindspot per seat, returned verbatim at BLINDSPOT_BOUNTY.
# The adversarial falsifier submits two -- the strong publication-bias attack
# and the original external-validity statement, which the boundary scientist
# independently repeats: a deliberate redundancy, the way a real multi-seat
# council nominates overlapping gaps (the bounty handler scores every
# nomination, so the duplicate is paid for in precision, not silently
# dropped).
BLINDSPOTS_BY_SEAT: dict[str, tuple[tuple[str, str, str], ...]] = {
    Seat.THEORY_BUILDER.value: (
        (
            "the biological mechanism linking screen time to depression is untested",
            "0.8",
            "0.7",
        ),
    ),
    Seat.CAUSAL_SCIENTIST.value: (
        (
            "residual confounding and reverse causation are not ruled out",
            "0.8",
            "0.7",
        ),
    ),
    Seat.MEASUREMENT_SCIENTIST.value: (
        ("screen time relies on self-report, a clear measurement bias", "0.8", "0.7"),
    ),
    Seat.REPLICATION_SCIENTIST.value: (
        ("no replication in an independent cohort exists", "0.7", "0.6"),
    ),
    Seat.BOUNDARY_SCIENTIST.value: (
        (
            "nearly every sample is from a western high income country, "
            "external validity untested",
            "0.7",
            "0.6",
        ),
    ),
    Seat.ADVERSARY_FALSIFIER.value: (
        (
            "the association may be entirely an artifact of publication bias",
            "0.8",
            "0.7",
        ),
        (
            "nearly every sample is from a western high income country, "
            "external validity untested",
            "0.7",
            "0.6",
        ),
    ),
    Seat.EVIDENCE_AUDITOR.value: (
        ("citation provenance for two sources cannot be verified", "0.6", "0.5"),
    ),
}


@dataclass(frozen=True, slots=True)
class Acquired:
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
class Refused:
    query: str
    reason: str


@dataclass(frozen=True, slots=True)
class AcquisitionResult:
    acquired: tuple[Acquired, ...] = ()
    refused: tuple[Refused, ...] = ()
    unresolvable: tuple[str, ...] = ()


class DemoAcquirer(SourceAcquirer):
    """Ignores what was actually requested and returns the same three sources.

    A scripted double answers with a fixed, known-shape result regardless of
    the request list's exact contents, since what is under test is what the
    round and the scoring functions do with an acquisition result, not a real
    retrieval query language.
    """

    def __init__(self) -> None:
        self.source_ids = {doi: uuid4() for doi in DOIS}

    async def acquire(self, requests: list[tuple[Seat, str]]) -> AcquisitionResult:
        return AcquisitionResult(
            acquired=tuple(
                Acquired(
                    source_id=self.source_ids[doi],
                    doi=doi,
                    title=f"A study at {doi}",
                    evidence_level="B",
                    authors=("A. Researcher",),
                    dataset_id=DATASET_IDS[doi],
                )
                for doi in DOIS
            )
        )

    async def acquire_uploaded(self, object_ids: tuple[UUID, ...]) -> AcquisitionResult:
        # Not exercised by the demo scenario -- see the module docstring.
        return AcquisitionResult()

    async def acquire_dois(self, dois: tuple[str, ...]) -> AcquisitionResult:
        return AcquisitionResult()

    async def acquire_knowledge_documents(
        self, documents: tuple[object, ...]
    ) -> AcquisitionResult:
        return AcquisitionResult()


class DemoAcquirerNoLineage(DemoAcquirer):
    """Acquisition without lineage metadata (design spec 11.4 ablation).

    Returns the same three sources with ``dataset_id`` stripped, so evidence
    independence (CLAUDE.md 7.4) cannot see that two of them share a dataset --
    exactly what a system with no lineage tracking would report: every source
    looks independent. The full variant passes :class:`DemoAcquirer`; the
    ``full_ablate_lineage`` ablation passes this one.
    """

    async def acquire(self, requests: list[tuple[Seat, str]]) -> AcquisitionResult:
        result = await super().acquire(requests)
        return AcquisitionResult(
            acquired=tuple(
                Acquired(
                    source_id=item.source_id,
                    doi=item.doi,
                    title=item.title,
                    evidence_level=item.evidence_level,
                    already_known=item.already_known,
                    authors=item.authors,
                    dataset_id=None,
                    object_id=item.object_id,
                    document_id=item.document_id,
                )
                for item in result.acquired
            ),
            refused=result.refused,
            unresolvable=result.unresolvable,
        )


@dataclass(frozen=True, slots=True)
class Extraction:
    ok: bool
    reason: str = ""
    finding_id: UUID | None = None
    evidence_level: str = ""
    exact_quote: str = ""
    finding_statement: str = ""
    method_quality: dict[str, float] = field(default_factory=dict)


class DemoFindingExtractor(FindingExtractor):
    """One clean extraction, one quote-less "ok" extraction, one outright failure.

    This mix is deliberate, not arbitrary: it is what makes citation
    entailment land on a real fraction (1 of 2 emitted StudyFinding events has
    a usable quote) instead of a vacuous 0.0 or 1.0 that a uniform fixture
    would produce.
    """

    def __init__(self) -> None:
        self._by_doi = {
            DOIS[0]: Extraction(
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
            DOIS[1]: Extraction(
                ok=True,
                finding_id=uuid4(),
                evidence_level="B",
                exact_quote="",
                finding_statement="A related cohort reports a similar pattern.",
                method_quality={"directness": 0.4},
            ),
            DOIS[2]: Extraction(ok=False, reason="no open access full text url"),
        }

    async def extract(self, source_id: UUID, doi: str) -> Extraction:
        return self._by_doi[doi]

    async def extract_uploaded(self, source_id: UUID, object_id: UUID) -> Extraction:
        # Not exercised by the demo scenario -- see the module docstring.
        raise NotImplementedError

    async def extract_knowledge_document(
        self, source_id: UUID, document_id: UUID
    ) -> Extraction:
        # Not exercised by the demo scenario -- see the module docstring.
        raise NotImplementedError


class DemoGateway:
    """Answers every phase the demo case needs; every other phase gets {}.

    Mirrors ``scripts/seed_demo_task.py``'s ``_Gateway`` -- the same scripted
    stand-in already proven against the real worker pipeline -- adapted to the
    smaller set of phases this case actually exercises. Every seat answers
    BLINDSPOT_BOUNTY with its own specialist nomination from
    ``BLINDSPOTS_BY_SEAT``, so a run's blindspot score tracks which seats
    participate instead of hinging on whether the adversarial falsifier is
    among them. The ADVERSARY_FALSIFIER seat's FINAL_REJUDGMENT answer
    contains "反对", which
    ``packages.council.rounds.final_rejudgment._detect_dissent`` matches, so
    this run also produces one genuine dissent with a matching
    DissentCertificate (dissent preservation's non-trivial path, not the
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
            payload = {"requests": [f"doi {doi}" for doi in DOIS]}
        elif phase is TaskPhase.BLINDSPOT_BOUNTY:
            nominated = BLINDSPOTS_BY_SEAT.get(seat, ())
            if nominated:
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
                        for statement, impact, uncertainty in nominated
                    ]
                }
        elif phase is TaskPhase.JOINT_MODELING:
            # The two required dialectical fields plus both optional ones, so
            # the handler is ready and the full system folds a DebateCapsule
            # (the plain-fold ablation then has something real to omit).
            payload = {
                "boundary_conditions": [
                    "limited to adolescent samples in high-income countries."
                ],
                "unresolved_conflicts": [
                    "effect direction differs by gender in two cohorts."
                ],
                "strongest_opposition_refs": [str(uuid4())],
                "falsification_conditions": [
                    "a preregistered RCT with adequate power would falsify it."
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
