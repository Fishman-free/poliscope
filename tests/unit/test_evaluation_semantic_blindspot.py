"""Unit tests for the LLM semantic judge over Blindspot Recall/Precision.

These drive :func:`packages.evaluation.semantic_blindspot.score_blindspots_semantic`
against a scripted :class:`ModelGateway` stub rather than a real provider, so
the judge's matching, its ``None``-on-unmeasurable contract, and its defensive
index handling are all pinned without a network or a key. The stub is the same
shape the production :class:`OpenAICompatibleModelGateway` returns, so the test
asserts what the scorer does with a gateway answer, not how a vendor phrased it.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest

from packages.evaluation.semantic_blindspot import score_blindspots_semantic
from packages.evidence.contracts import EvidenceNodeType
from packages.evidence.ledger import LedgerEntry
from packages.evidence.sql_projector import STATUS_ADMITTED
from packages.kernel.contracts import FrozenDict
from packages.models.contracts import (
    ModelClass,
    ModelGateway,
    ModelRequest,
    ModelResult,
    SchemaStatus,
)

_TASK_ID = uuid4()


def _blindspot(statement: str, *, status: str = STATUS_ADMITTED) -> LedgerEntry:
    return LedgerEntry(
        event_id=uuid4(),
        task_id=_TASK_ID,
        event_type=EvidenceNodeType.BLINDSPOT.value,
        payload={"statement": statement},
        idempotency_key=f"key-{uuid4()}",
        sequence=1,
        status=status,
    )


class _ScriptedJudge(ModelGateway):
    """Returns a fixed index list per concept, keyed by concept text.

    Concepts not in ``answers`` get an empty list (the judge says "none match"),
    matching the real judge's contract. ``fail_on`` names concepts whose call
    should raise, and ``quarantine`` flips every answer to schema-quarantined.
    """

    def __init__(
        self,
        answers: dict[str, list[int]],
        *,
        fail_on: frozenset[str] = frozenset(),
        quarantine: bool = False,
    ) -> None:
        self._answers = answers
        self._fail_on = fail_on
        self._quarantine = quarantine

    async def invoke(self, request: ModelRequest) -> ModelResult:
        concept = _concept_from(request)
        if concept in self._fail_on:
            raise RuntimeError("judge transport failure")
        if self._quarantine:
            return ModelResult(
                call_id=uuid4(),
                payload=FrozenDict({}),
                input_tokens=0,
                output_tokens=0,
                cost_usd=Decimal("0"),
                latency_ms=1,
                retries=0,
                schema_status=SchemaStatus.QUARANTINED,
            )
        return ModelResult(
            call_id=uuid4(),
            payload=FrozenDict(
                {"addressed_statement_indices": self._answers.get(concept, [])}
            ),
            input_tokens=0,
            output_tokens=0,
            cost_usd=Decimal("0"),
            latency_ms=1,
            retries=0,
            schema_status=SchemaStatus.OK,
        )


def _concept_from(request: ModelRequest) -> str:
    user = next(
        message.content for message in request.messages if message.role == "user"
    )
    marker = "Expected blindspot concept: "
    start = user.index(marker) + len(marker)
    return user[start : user.index("\n", start)].strip()


async def test_semantic_matches_paraphrase_keyword_cannot() -> None:
    """The judge matches meaning the keyword heuristic would miss.

    The statement never contains the literal tokens "reverse" or "causation",
    yet it names the reverse-causation gap -- exactly the real-model case the
    keyword proxy collapses on.
    """
    statements = [_blindspot("the direction of effect could run the other way")]
    gateway = _ScriptedJudge({"reverse_causation": [0]})
    result = await score_blindspots_semantic(
        statements, ("reverse_causation",), gateway
    )
    assert result == (1.0, 1.0)


async def test_semantic_precision_penalises_unmatched_statement() -> None:
    statements = [
        _blindspot("the direction of effect could run the other way"),
        _blindspot("a completely unrelated gap about funding sources"),
    ]
    gateway = _ScriptedJudge({"reverse_causation": [0]})
    recall, precision = await score_blindspots_semantic(
        statements, ("reverse_causation",), gateway
    )
    assert recall == 1.0
    assert precision == 0.5


async def test_semantic_recall_misses_unaddressed_concept() -> None:
    statements = [_blindspot("the direction of effect could run the other way")]
    gateway = _ScriptedJudge({"reverse_causation": [0], "provenance": []})
    recall, precision = await score_blindspots_semantic(
        statements, ("reverse_causation", "provenance"), gateway
    )
    assert recall == 0.5
    assert precision == 1.0


async def test_semantic_none_when_nothing_to_compare() -> None:
    gateway = _ScriptedJudge({})
    assert await score_blindspots_semantic([], ("x",), gateway) is None
    assert (
        await score_blindspots_semantic(
            [_blindspot("a statement")], (), gateway
        )
        is None
    )


async def test_semantic_none_on_transport_failure() -> None:
    """A judge that cannot be reached is unmeasured, not recall 0.0."""
    statements = [_blindspot("the direction of effect could run the other way")]
    gateway = _ScriptedJudge({}, fail_on=frozenset({"reverse_causation"}))
    with pytest.raises(RuntimeError):
        await score_blindspots_semantic(statements, ("reverse_causation",), gateway)


async def test_semantic_none_on_quarantined_answer() -> None:
    statements = [_blindspot("the direction of effect could run the other way")]
    gateway = _ScriptedJudge({}, quarantine=True)
    with pytest.raises(RuntimeError):
        await score_blindspots_semantic(statements, ("reverse_causation",), gateway)


async def test_semantic_drops_out_of_range_indices() -> None:
    statements = [_blindspot("the direction of effect could run the other way")]
    gateway = _ScriptedJudge({"reverse_causation": [0, 7, -1]})
    recall, precision = await score_blindspots_semantic(
        statements, ("reverse_causation",), gateway
    )
    assert recall == 1.0
    assert precision == 1.0


async def test_semantic_excludes_quarantined_statements() -> None:
    statements = [
        _blindspot("the direction of effect could run the other way"),
        _blindspot("quarantined blindspot", status="quarantined"),
    ]
    gateway = _ScriptedJudge({"reverse_causation": [0]})
    recall, precision = await score_blindspots_semantic(
        statements, ("reverse_causation",), gateway
    )
    assert recall == 1.0
    assert precision == 1.0


def test_model_class_is_medium() -> None:
    """The judge uses the cheap MEDIUM tier, not strong reasoning."""
    assert ModelClass.MEDIUM.value == "medium"
