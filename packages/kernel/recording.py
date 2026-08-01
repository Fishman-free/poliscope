"""Stable lookup keys for recorded gateway replay.

A recorded gateway is only deterministic if its lookup key depends solely on
fields that stay identical across runs. Hashing a whole request would fold in
per-run identity such as ``task_id``, which makes a frozen recording
unmatchable on the next process and silently breaks Replay.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from typing import cast

from packages.kernel.contracts import ContractModel, thaw_for_serialization


def stable_request_hash(
    request: ContractModel,
    *,
    exclude: Iterable[str] = (),
) -> str:
    """Hash the run-stable fields of a gateway request.

    Args:
        request: The gateway request contract to key.
        exclude: Field names that vary per run and must not affect the key.
    """
    payload = cast(
        dict[str, object],
        thaw_for_serialization(request.model_dump(mode="json")),
    )
    excluded = frozenset(exclude)
    unknown = excluded - payload.keys()
    if unknown:
        raise KeyError(
            f"cannot exclude unknown request fields: {sorted(unknown)}"
        )
    stable = {key: value for key, value in payload.items() if key not in excluded}
    normalized = json.dumps(stable, sort_keys=True).encode("utf-8")
    return hashlib.sha256(normalized).hexdigest()
