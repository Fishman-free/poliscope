from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from packages.kernel.contracts import FrozenDict
from packages.research.contracts import (
    EvidenceDemandType,
    ResearchBudget,
    ResearchContract,
    ResearchScope,
    TaskModelConfig,
)


def test_evidence_demand_type_has_exactly_seven_required_values() -> None:
    assert tuple(member.value for member in EvidenceDemandType) == (
        "CORRELATION",
        "CAUSAL_OR_REVERSE_CAUSAL",
        "MEASUREMENT",
        "REPLICATION",
        "BOUNDARY",
        "MECHANISM",
        "NULL_OR_COUNTEREXAMPLE",
    )


def test_typed_evidence_demand_remains_enum_and_serializes_as_value() -> None:
    scope = ResearchScope(
        populations=("adolescents",),
        regions=("global",),
        languages=("en",),
        date_from=None,
        date_until=date(2025, 1, 1),
        evidence_priorities=(EvidenceDemandType.CORRELATION,),
        allow_preprints=False,
    )

    assert scope.evidence_priorities[0] is EvidenceDemandType.CORRELATION
    assert scope.model_dump(mode="json")["evidence_priorities"] == ["CORRELATION"]
    assert hash(scope.evidence_priorities)


def test_research_contract_requires_question_scope_budget_and_inputs() -> None:
    with pytest.raises(ValidationError):
        ResearchContract.model_validate({"question": "数字行为是否影响心理健康？"})


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("wall_clock_minutes", 0),
        ("model_cost_usd", Decimal("-0.01")),
        ("tool_call_limit", 0),
        ("source_limit", -1),
    ),
)
def test_research_budget_rejects_non_positive_limits_and_negative_cost(
    field: str, value: int | Decimal
) -> None:
    values = {
        "wall_clock_minutes": 60,
        "model_cost_usd": Decimal("1.00"),
        "tool_call_limit": 10,
        "source_limit": 5,
    }
    values[field] = value

    with pytest.raises(ValidationError):
        ResearchBudget.model_validate(values)


@pytest.mark.parametrize(
    "field", ("wall_clock_minutes", "tool_call_limit", "source_limit")
)
def test_research_budget_rejects_boolean_integer_limits(field: str) -> None:
    values = {
        "wall_clock_minutes": 60,
        "model_cost_usd": Decimal("1.00"),
        "tool_call_limit": 10,
        "source_limit": 5,
    }
    values[field] = True

    with pytest.raises(ValidationError):
        ResearchBudget.model_validate(values)


def test_research_scope_rejects_reversed_date_range() -> None:
    with pytest.raises(ValidationError):
        ResearchScope(
            populations=("adolescents",),
            regions=("global",),
            languages=("en",),
            date_from=date(2025, 1, 2),
            date_until=date(2025, 1, 1),
            evidence_priorities=(EvidenceDemandType.CORRELATION,),
            allow_preprints=False,
        )


def test_research_contract_rejects_unknown_nested_fields(
    valid_research_contract: ResearchContract,
) -> None:
    payload = valid_research_contract.model_dump(mode="python")
    scope = dict(payload["scope"])
    scope["unexpected"] = True
    payload["scope"] = scope

    with pytest.raises(ValidationError):
        ResearchContract.model_validate(payload)


def test_research_contract_from_plain_containers_is_immutable(
    valid_research_contract: ResearchContract,
) -> None:
    assert isinstance(valid_research_contract.scope.languages, tuple)
    assert isinstance(valid_research_contract.scope.evidence_priorities, tuple)
    assert isinstance(valid_research_contract.user_evidence.dois, tuple)
    assert isinstance(valid_research_contract.model_extra, FrozenDict | type(None))


def test_task_model_config_defaults_model_name_to_none() -> None:
    config = TaskModelConfig(
        base_url="https://api.deepseek.com", api_key="sk-test"
    )
    assert config.model_name is None


@pytest.mark.parametrize(
    "payload",
    (
        {"base_url": "api.deepseek.com", "api_key": "sk-test"},  # 缺 scheme
        {"base_url": "", "api_key": "sk-test"},  # 空 URL
        {"base_url": "https://api.deepseek.com", "api_key": ""},  # 空密钥
    ),
)
def test_task_model_config_rejects_broken_endpoint_pairs(
    payload: dict[str, str],
) -> None:
    with pytest.raises(ValidationError):
        TaskModelConfig.model_validate(payload)


def test_research_contract_serializes_to_json_compatible_values(
    valid_research_contract: ResearchContract,
) -> None:
    dumped = valid_research_contract.model_dump(mode="json")

    assert dumped["scope"]["date_from"] == "2020-01-01"
    assert dumped["scope"]["date_until"] == "2025-12-31"
    assert dumped["scope"]["evidence_priorities"] == [
        "CAUSAL_OR_REVERSE_CAUSAL",
        "MEASUREMENT",
    ]
    assert dumped["budget"]["model_cost_usd"] == "25.00"
