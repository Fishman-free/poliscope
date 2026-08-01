from __future__ import annotations

from packages.epistemo.stopping import StopReason, decide_stop


def test_budget_exhaustion_is_not_saturation() -> None:
    result = decide_stop(
        no_new_information_rounds=0, budget_remaining=0
    )
    assert result.reason == StopReason.BUDGET_EXHAUSTED


def test_evidence_saturation_when_no_new_info() -> None:
    result = decide_stop(
        no_new_information_rounds=1, budget_remaining=10
    )
    assert result.reason == StopReason.EVIDENCE_SATURATION


def test_continue_when_budget_and_info_remain() -> None:
    result = decide_stop(
        no_new_information_rounds=0, budget_remaining=10
    )
    assert result.reason == StopReason.CONTINUE
