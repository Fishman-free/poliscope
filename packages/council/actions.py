from __future__ import annotations

from packages.council.contracts import (
    ALLOWED_ACTIONS,
    ALLOWED_RESPONSES,
    ChallengeResponseType,
    ScientificActionType,
)


def validate_action(action_type: ScientificActionType) -> None:
    if action_type not in ALLOWED_ACTIONS:
        raise ValueError(f"disallowed action: {action_type}")


def validate_response(response_type: ChallengeResponseType) -> None:
    if response_type not in ALLOWED_RESPONSES:
        raise ValueError(f"disallowed response: {response_type}")
