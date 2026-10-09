"""Audit metadata must never duplicate prompts or tool input values."""

from __future__ import annotations

from uuid import uuid4

from packages.models.audit import _model_request_summary, _tool_request_summary
from packages.models.contracts import ModelClass, ModelMessage, ModelRequest
from packages.tools.contracts import ToolRequest


def test_model_audit_summary_excludes_nested_sensitive_text() -> None:
    secret = "private-patient-email@example.test"
    request = ModelRequest(
        task_id=uuid4(),
        actor="causal_scientist",
        purpose="cross_examination",
        model_class=ModelClass.STRONG_REASONING,
        messages=(ModelMessage(role="user", content=f"Study text: {secret}"),),
        output_schema="ChallengeSet",
        evidence_refs=(),
    )

    summary = _model_request_summary(request)

    assert summary["message_count"] == 1
    assert "messages" not in summary
    assert secret not in str(summary)


def test_tool_audit_summary_excludes_argument_values() -> None:
    secret = "https://private.example.test/file?token=secret"
    request = ToolRequest(
        task_id=uuid4(),
        actor="evidence_auditor",
        tool_name="fetcher",
        operation="download",
        arguments={"nested": {"signed_url": secret}},
    )

    summary = _tool_request_summary(request)

    assert summary["argument_count"] == 1
    assert "arguments" not in summary
    assert secret not in str(summary)
