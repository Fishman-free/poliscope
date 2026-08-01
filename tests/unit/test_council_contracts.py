from __future__ import annotations

import pytest

from packages.council.contracts import (
    ALL_SEATS,
    ChallengeResponseType,
    ScientificActionType,
    Seat,
)
from packages.council.roles import ROLE_SPECS


def test_registry_contains_exactly_seven_scientists() -> None:
    assert set(ROLE_SPECS) == set(Seat)
    assert len(ROLE_SPECS) == 7
    assert "epistemo_brain" not in {seat.value for seat in Seat}


def test_all_seats_frozen_set_has_seven() -> None:
    assert len(ALL_SEATS) == 7


@pytest.mark.parametrize(
    "action",
    [
        "PROPOSE",
        "SUPPORT",
        "CHALLENGE",
        "QUALIFY",
        "FORK",
        "REQUEST",
        "REVISE",
        "DISSENT",
    ],
)
def test_allowed_actions(action) -> None:
    assert ScientificActionType(action)


@pytest.mark.parametrize(
    "response", ["DEFEND", "REVISE", "NARROW", "WITHDRAW", "DISSENT"]
)
def test_challenge_response_whitelist(response) -> None:
    assert ChallengeResponseType(response)
