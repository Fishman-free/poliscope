"""Storage and agreement computation for human annotation (C9).

This is the pipeline that ``packages.evaluation.agreement`` was always written
to consume but had no data from. A batch freezes the items under review
(copied from the evidence graph, so a later projection cannot rewrite what was
rated); raters upsert one nominal label per item; and agreement is computed
from the labels with the existing, unit-tested Cohen's kappa (two raters) and
Krippendorff's alpha (three or more).

Agreement is a measurement about the *raters*, never evidence about the claims:
nothing here writes the evidence graph, and an under-labelled batch returns
``None`` for its score rather than a fabricated number (CLAUDE.md 7).

Output contract: every dict carries BOTH the web client's keys
(batch_id/item_count/rater_count and agreement.value/raters/note, matching
AnnotationBatchSummary/AnnotationBatchDetail in apps/web/src/api/types.ts) and
the older id/score/rater_count keys so non-web consumers and the integration
suite keep working. A contract mismatch here used to make the web view read
``undefined.slice`` / ``undefined.length`` right after "创建批次" and crash to
the error boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from packages.evaluation.agreement import (
    cohen_kappa,
    krippendorff_alpha_nominal,
)
from packages.evaluation.annotation_models import (
    ANNOTATION_LABELS,
    AnnotationBatchModel,
    AnnotationItemModel,
    AnnotationLabelModel,
)


class AnnotationError(Exception):
    """Raised on an invalid annotation request (unknown batch/item/label)."""


@dataclass(frozen=True, slots=True)
class NewAnnotationItem:
    ref_kind: str
    ref_node_id: str
    statement: str
    position: dict[str, object]


def _validate_label(label: str) -> None:
    if label not in ANNOTATION_LABELS:
        raise AnnotationError(
            f"label must be one of {ANNOTATION_LABELS}, got {label!r}"
        )


async def create_batch(
    session: AsyncSession,
    task_id: UUID,
    created_by: str,
    items: list[NewAnnotationItem],
    title: str = "",
    note: str = "",
) -> UUID:
    """Create a batch and its frozen items in one transaction."""
    if not items:
        raise AnnotationError("a batch needs at least one item")
    batch_id = uuid4()
    session.add(
        AnnotationBatchModel(
            id=batch_id,
            task_id=task_id,
            created_by=created_by,
            title=title,
            note=note,
        )
    )
    for item in items:
        session.add(
            AnnotationItemModel(
                id=uuid4(),
                batch_id=batch_id,
                ref_kind=item.ref_kind,
                ref_node_id=item.ref_node_id,
                statement=item.statement,
                position=dict(item.position),
            )
        )
    await session.flush()
    return batch_id


async def list_batches(
    session: AsyncSession, task_id: UUID
) -> list[dict[str, object]]:
    """All batches for a task, each with item/rater counts.

    Two bounded grouped queries supply the counts (one item query, one label
    query for every batch at once) instead of N+1 round trips.
    """
    batch_rows = list(
        (
            await session.execute(
                select(AnnotationBatchModel)
                .where(AnnotationBatchModel.task_id == task_id)
                .order_by(AnnotationBatchModel.created_at)
            )
        )
        .scalars()
    )
    if not batch_rows:
        return []
    batch_ids = [row.id for row in batch_rows]
    item_counts: dict[UUID, int] = {}
    item_to_batch: dict[UUID, UUID] = {}
    item_rows = await session.execute(
        select(AnnotationItemModel.id, AnnotationItemModel.batch_id).where(
            AnnotationItemModel.batch_id.in_(batch_ids)
        )
    )
    for item_id, owner_batch_id in item_rows:
        item_to_batch[item_id] = owner_batch_id
        item_counts[owner_batch_id] = item_counts.get(owner_batch_id, 0) + 1
    raters_by_batch: dict[UUID, set[str]] = {}
    if item_to_batch:
        label_rows = await session.execute(
            select(
                AnnotationLabelModel.rater_name, AnnotationLabelModel.item_id
            ).where(
                AnnotationLabelModel.item_id.in_(list(item_to_batch.keys()))
            )
        )
        for rater_name, owner_item_id in label_rows:
            owner_batch_id = item_to_batch[owner_item_id]
            raters_by_batch.setdefault(owner_batch_id, set()).add(rater_name)
    return [
        {
            # New web contract
            "batch_id": str(row.id),
            "task_id": str(row.task_id),
            "title": row.title,
            "created_by": row.created_by,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "item_count": item_counts.get(row.id, 0),
            "rater_count": len(raters_by_batch.get(row.id, set())),
            # Legacy keys retained for non-web consumers / integration tests
            "id": str(row.id),
            "note": row.note,
        }
        for row in batch_rows
    ]


async def _batch_or_raise(
    session: AsyncSession, batch_id: UUID
) -> AnnotationBatchModel:
    batch = await session.get(AnnotationBatchModel, batch_id)
    if batch is None:
        raise AnnotationError(f"unknown annotation batch {batch_id}")
    return batch


async def upsert_label(
    session: AsyncSession,
    batch_id: UUID,
    item_id: UUID,
    rater_name: str,
    label: str,
    note: str = "",
) -> None:
    """Insert or revise one rater's label for one item (one label per rater)."""
    rater_name = rater_name.strip()
    if not rater_name:
        raise AnnotationError("rater_name must not be empty")
    _validate_label(label)
    await _batch_or_raise(session, batch_id)
    item = await session.get(AnnotationItemModel, item_id)
    if item is None or item.batch_id != batch_id:
        raise AnnotationError(
            f"item {item_id} does not belong to batch {batch_id}"
        )
    # Upsert on the (item_id, rater_name) unique constraint: a rater revising
    # their judgment replaces label/note rather than double-counting.
    await session.execute(
        pg_insert(AnnotationLabelModel)
        .values(
            id=uuid4(),
            item_id=item_id,
            rater_name=rater_name,
            label=label,
            note=note,
        )
        .on_conflict_do_update(
            constraint="uq_annotation_label_one_per_rater",
            set_={"label": label, "note": note},
        )
    )
    await session.flush()


def _undefined_agreement(
    raters: list[str], labeled: int, note: str
) -> dict[str, object]:
    """Shape used when agreement is not yet mathematically defined.

    The web client renders an object (never a missing key); value/score stay
    null instead of fabricating a number.
    """
    return {
        "method": "undefined",
        "value": None,
        "score": None,
        "raters": len(raters),
        "rater_count": len(raters),
        "rater_names": raters,
        "items_with_two_or_more": labeled,
        "note": note,
    }


def _agreement(
    items: list[AnnotationItemModel],
    labels_by_item: dict[UUID, list[AnnotationLabelModel]],
) -> dict[str, object]:
    """Compute inter-rater agreement; null value when it is not defined."""
    raters = sorted(
        {
            label.rater_name
            for item in items
            for label in labels_by_item.get(item.id, [])
        }
    )
    if len(raters) < 2:
        return _undefined_agreement(
            raters,
            0,
            "At least two distinct raters are required.",
        )
    # coders[r][j] = rater r's label for item j, or None when unlabelled.
    coders: list[list[str | None]] = []
    for rater in raters:
        row: list[str | None] = []
        for item in items:
            match = [
                label.label
                for label in labels_by_item.get(item.id, [])
                if label.rater_name == rater
            ]
            row.append(match[0] if match else None)
        coders.append(row)
    labeled = sum(
        1
        for j in range(len(items))
        if sum(1 for coder in coders if coder[j] is not None) >= 2
    )
    if labeled == 0:
        return _undefined_agreement(
            raters,
            0,
            "At least two raters must label the same item.",
        )
    try:
        if len(raters) == 2:
            # Cohen's kappa needs both raters on the same ordered items; restrict
            # to items both labelled, keeping alignment.
            # Walrus bindings narrow the labels to non-None str, so the
            # paired lists type-check as list[str] for cohen_kappa.
            paired: list[tuple[str, str]] = [
                (a, b)
                for j in range(len(items))
                if (a := coders[0][j]) is not None
                and (b := coders[1][j]) is not None
            ]
            if len(paired) < 1:
                return _undefined_agreement(
                    raters, labeled, "No item was labelled by both raters."
                )
            score = cohen_kappa(
                [a for a, _ in paired], [b for _, b in paired]
            )
            # The web client compares against the short method ids exactly.
            method = "cohen_kappa"
        else:
            score = krippendorff_alpha_nominal(coders)
            method = "krippendorff_alpha"
    except ValueError:
        return _undefined_agreement(
            raters,
            labeled,
            "Agreement is undefined for the current label pattern.",
        )
    rounded = round(float(score), 4)
    return {
        "method": method,
        # New web contract
        "value": rounded,
        "raters": len(raters),
        "note": None,
        # Legacy keys
        "score": rounded,
        "rater_count": len(raters),
        "rater_names": raters,
        "items_with_two_or_more": labeled,
    }


async def get_batch_detail(
    session: AsyncSession,
    batch_id: UUID,
) -> dict[str, object]:
    """Return the batch, its items, every label, and current agreement."""
    batch = await _batch_or_raise(session, batch_id)
    item_rows = await session.execute(
        select(AnnotationItemModel)
        .where(AnnotationItemModel.batch_id == batch_id)
        .order_by(AnnotationItemModel.created_at, AnnotationItemModel.id)
    )
    items = list(item_rows.scalars())
    # No items -> no filtering conditions (an empty IN clause would be invalid
    # SQL and a bare ``False`` does not type-check as a SQL expression).
    label_conditions = (
        [AnnotationLabelModel.item_id.in_([item.id for item in items])]
        if items
        else []
    )
    label_rows = await session.execute(
        select(AnnotationLabelModel)
        .where(*label_conditions)
        .order_by(AnnotationLabelModel.created_at)
    )
    labels_by_item: dict[UUID, list[AnnotationLabelModel]] = {}
    for label in label_rows.scalars():
        labels_by_item.setdefault(label.item_id, []).append(label)

    items_out = [
        {
            "id": str(item.id),
            "ref_kind": item.ref_kind,
            "ref_node_id": (
                str(item.ref_node_id) if item.ref_node_id else None
            ),
            "statement": item.statement,
            "position": dict(item.position),
            "labels": [
                {
                    "rater_name": label.rater_name,
                    "label": label.label,
                    "note": label.note,
                    "created_at": (
                        label.created_at.isoformat()
                        if label.created_at
                        else None
                    ),
                }
                for label in labels_by_item.get(item.id, [])
            ],
        }
        for item in items
    ]
    agreement = _agreement(items, labels_by_item)
    return {
        # New web contract
        "batch_id": str(batch.id),
        "task_id": str(batch.task_id),
        "title": batch.title,
        "created_by": batch.created_by,
        "created_at": batch.created_at.isoformat() if batch.created_at else None,
        "item_count": len(items),
        "rater_count": agreement["raters"],
        "items": items_out,
        "agreement": agreement,
        # Legacy keys
        "id": str(batch.id),
        "note": batch.note,
    }


__all__ = [
    "AnnotationError",
    "NewAnnotationItem",
    "create_batch",
    "get_batch_detail",
    "list_batches",
    "upsert_label",
]
