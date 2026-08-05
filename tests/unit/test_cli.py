"""Tests for the CLI adapter layer.

The CLI is the only surface a researcher can drive without a browser, so a
subcommand that parses but dispatches nowhere is a silent failure. These tests
assert that every subcommand reaches a handler and that failures map onto the
published exit codes rather than a traceback.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from apps.cli import exit_codes
from apps.cli.client import APIError, APIUnreachable, CLIClient, _is_loopback
from apps.cli.main import _auth_from_env, build_parser, main

SUBCOMMANDS: tuple[tuple[list[str], dict[str, object]], ...] = (
    (["health"], {}),
    (["start", "--contract", "c.json"], {"contract": "c.json"}),
    (
        ["confirm-claims", "--task-id", "t", "--claim-ids", "a", "b"],
        {"task_id": "t", "claim_ids": ["a", "b"]},
    ),
    (["status", "--task-id", "t"], {"task_id": "t"}),
    (["pause", "--task-id", "t"], {"task_id": "t"}),
    (["resume", "--task-id", "t"], {"task_id": "t"}),
    (["watch", "--task-id", "t"], {"task_id": "t"}),
    (["export", "--task-id", "t"], {"task_id": "t", "export_format": "markdown"}),
    (["council-preview", "--task-id", "t"], {"task_id": "t"}),
    (
        ["council-guidance", "--task-id", "t", "--text", "narrow the scope"],
        {"task_id": "t", "guidance_text": "narrow the scope"},
    ),
    (
        ["council-guidance", "--task-id", "t", "--text", ""],
        {"task_id": "t", "guidance_text": ""},
    ),
)


@pytest.mark.parametrize(("argv", "expected"), SUBCOMMANDS)
def test_every_subcommand_binds_a_handler(
    argv: list[str],
    expected: dict[str, Any],
) -> None:
    """A parsed subcommand must carry the callable that performs it.

    Without this binding the CLI exits zero while doing nothing, which is how
    the earlier stub behaved.
    """
    args = build_parser().parse_args(argv)
    assert callable(getattr(args, "handler", None))
    for field, value in expected.items():
        assert getattr(args, field) == value


def test_no_command_prints_help_and_reports_usage_error() -> None:
    assert main([]) == exit_codes.USAGE


def test_unreachable_api_reports_its_own_exit_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A refused connection must be distinguishable from a rejected request."""

    async def fail(*_: object, **__: object) -> object:
        raise APIUnreachable("no listener")

    monkeypatch.setattr(CLIClient, "health", fail)
    assert main(["health"]) == exit_codes.UNREACHABLE


def test_rejected_request_reports_its_own_exit_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def reject(*_: object, **__: object) -> object:
        raise APIError(404, "unknown task")

    monkeypatch.setattr(CLIClient, "workspace", reject)
    assert main(["status", "--task-id", "missing"]) == exit_codes.REQUEST_REJECTED


def test_unreadable_contract_fails_before_any_request(tmp_path: Path) -> None:
    """A missing contract must not be reported as an API problem."""
    assert (
        main(["start", "--contract", str(tmp_path / "absent.json")])
        == exit_codes.FAILED
    )


def test_malformed_contract_fails_before_any_request(tmp_path: Path) -> None:
    contract = tmp_path / "broken.json"
    contract.write_text("{not json", encoding="utf-8")
    assert main(["start", "--contract", str(contract)]) == exit_codes.FAILED


def test_status_renders_snapshot_fields(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Paper count and independent cluster count must both be shown.

    CLAUDE.md 7.4 requires the two numbers side by side so that a reader cannot
    mistake paper volume for evidence volume.
    """

    async def snapshot(*_: object, **__: object) -> dict[str, Any]:
        return {
            "task": {"task_id": "t", "status": "RUNNING"},
            "workspace_version": 7,
            "paper_count": 12,
            "independent_cluster_count": 4,
            "blindspots": [{}, {}],
            "dissents": [{}],
            "safety_notice": {"medical_disclaimer": "not medical advice"},
        }

    monkeypatch.setattr(CLIClient, "workspace", snapshot)
    assert main(["status", "--task-id", "t"]) == exit_codes.OK
    out = capsys.readouterr().out
    assert "12 papers / 4 independent clusters" in out
    assert "not medical advice" in out


def test_json_flag_emits_parseable_output(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    async def snapshot(*_: object, **__: object) -> dict[str, Any]:
        return {"task": {"task_id": "t"}, "workspace_version": 1}

    monkeypatch.setattr(CLIClient, "workspace", snapshot)
    assert main(["--json", "status", "--task-id", "t"]) == exit_codes.OK
    assert json.loads(capsys.readouterr().out)["workspace_version"] == 1


def test_export_writes_requested_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    async def export(*_: object, **__: object) -> str:
        return "# Research Brief\n"

    monkeypatch.setattr(CLIClient, "export", export)
    destination = tmp_path / "brief.md"
    assert (
        main(["export", "--task-id", "t", "--output", str(destination)])
        == exit_codes.OK
    )
    assert destination.read_text(encoding="utf-8") == "# Research Brief\n"


async def test_pause_and_resume_request_the_endpoints_that_actually_exist() -> None:
    """Same failure mode as export: a stubbed client cannot catch a dead route."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"task_id": "abc", "status": "PAUSED"})

    transport = httpx.MockTransport(handler)
    async with CLIClient("http://poliscope.test", transport=transport) as client:
        await client.pause("abc")
        await client.resume("abc")

    assert [request.method for request in seen] == ["POST", "POST"]
    assert [request.url.path for request in seen] == [
        "/api/tasks/abc/pause",
        "/api/tasks/abc/resume",
    ]


def test_pause_command_prints_the_new_status(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    async def pause(*_: object, **__: object) -> dict[str, Any]:
        return {"task_id": "t", "status": "PAUSED"}

    monkeypatch.setattr(CLIClient, "pause", pause)
    assert main(["pause", "--task-id", "t"]) == exit_codes.OK
    assert "PAUSED" in capsys.readouterr().out


async def test_export_requests_the_endpoint_that_actually_exists() -> None:
    """The URL, not just the fact that a request was formed.

    ``export`` pointed at ``/api/tasks/{id}/export``, which has never existed,
    so every export ended in a 404 -- and the test above did not catch it
    because it stubbed the client out entirely. This one drives the real
    request through a transport and asserts where it went.
    """
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, text="# Research Brief\n")

    transport = httpx.MockTransport(handler)
    async with CLIClient("http://poliscope.test", transport=transport) as client:
        body = await client.export("abc", "markdown")

    assert body == "# Research Brief\n"
    assert seen[0].url.path == "/api/reports/abc"
    assert seen[0].url.params["format"] == "markdown"


async def test_council_preview_requests_the_endpoint_that_actually_exists() -> None:
    """Same failure mode as export/pause: a stubbed client can't catch a dead route."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={"task_id": "abc", "status": "AWAITING_COUNCIL_INPUT", "seats": []},
        )

    transport = httpx.MockTransport(handler)
    async with CLIClient("http://poliscope.test", transport=transport) as client:
        await client.council_preview("abc")

    assert seen[0].method == "GET"
    assert seen[0].url.path == "/api/tasks/abc/council-preview"


async def test_council_guidance_requests_the_real_endpoint_and_body() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"task_id": "abc", "status": "QUEUED"})

    transport = httpx.MockTransport(handler)
    async with CLIClient("http://poliscope.test", transport=transport) as client:
        await client.council_guidance("abc", "narrow the scope")

    assert seen[0].method == "POST"
    assert seen[0].url.path == "/api/tasks/abc/council-guidance"
    assert json.loads(seen[0].content)["guidance_text"] == "narrow the scope"


def test_council_preview_renders_seat_positions(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    async def preview(*_: object, **__: object) -> dict[str, Any]:
        return {
            "task_id": "t",
            "status": "AWAITING_COUNCIL_INPUT",
            "seats": [
                {
                    "seat": "theory_builder",
                    "precommitment": {
                        "confidence": 0.4,
                        "update_condition": "a preregistered cohort study",
                    },
                    "challenges_raised": [{"is_fatal": False}],
                    "final_judgment": None,
                    "unavailable_phases": [],
                },
            ],
        }

    monkeypatch.setattr(CLIClient, "council_preview", preview)
    assert main(["council-preview", "--task-id", "t"]) == exit_codes.OK
    out = capsys.readouterr().out
    assert "AWAITING_COUNCIL_INPUT" in out
    assert "theory_builder" in out
    assert "0.4" in out
    assert "a preregistered cohort study" in out


@pytest.mark.parametrize(
    ("guidance_text", "expected_phrase"),
    (
        ("narrow the scope", "submitted"),
        ("", "none (continuing without intervention)"),
    ),
)
def test_council_guidance_command_reports_whether_it_steered(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    guidance_text: str,
    expected_phrase: str,
) -> None:
    """CLAUDE.md 4/8: declining to steer must read as a deliberate choice."""

    async def guidance(*_: object, **__: object) -> dict[str, Any]:
        return {"task_id": "t", "status": "QUEUED"}

    monkeypatch.setattr(CLIClient, "council_guidance", guidance)
    assert (
        main(["council-guidance", "--task-id", "t", "--text", guidance_text])
        == exit_codes.OK
    )
    assert expected_phrase in capsys.readouterr().out


@pytest.mark.parametrize(
    ("base_url", "expected"),
    (
        ("http://localhost:8000", True),
        ("http://127.0.0.1:8000", True),
        ("https://poliscope.example.org", False),
    ),
)
def test_loopback_detection_decides_proxy_trust(
    base_url: str,
    expected: bool,
) -> None:
    """Loopback traffic must bypass an ambient HTTP_PROXY.

    Otherwise a proxy answers for the not-yet-started API and the CLI reports a
    rejected request instead of an unreachable one.
    """
    assert _is_loopback(base_url) is expected


def test_auth_from_env_returns_none_when_credentials_are_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unset is the default -- an un-gated local API must see no change."""
    monkeypatch.delenv("POLISCOPE_API_USERNAME", raising=False)
    monkeypatch.delenv("POLISCOPE_API_PASSWORD", raising=False)
    assert _auth_from_env() is None


def test_auth_from_env_builds_basic_auth_when_credentials_are_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("POLISCOPE_API_USERNAME", "poliscope")
    monkeypatch.setenv("POLISCOPE_API_PASSWORD", "s3cret")
    assert isinstance(_auth_from_env(), httpx.BasicAuth)


async def test_env_credentials_add_an_authorization_header_and_absence_omits_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A Caddy-gated deployment 401s without this header.

    Every request must carry it when ``POLISCOPE_API_USERNAME`` /
    ``POLISCOPE_API_PASSWORD`` are set, and carry nothing when they are not --
    the unauthenticated case is today's behaviour and must not regress.
    """
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"status": "ok", "database": "ok"})

    transport = httpx.MockTransport(handler)

    monkeypatch.setenv("POLISCOPE_API_USERNAME", "poliscope")
    monkeypatch.setenv("POLISCOPE_API_PASSWORD", "s3cret")
    async with CLIClient(
        "http://poliscope.test", transport=transport, auth=_auth_from_env()
    ) as client:
        await client.health()
    assert "authorization" in seen[-1].headers

    monkeypatch.delenv("POLISCOPE_API_USERNAME", raising=False)
    monkeypatch.delenv("POLISCOPE_API_PASSWORD", raising=False)
    async with CLIClient(
        "http://poliscope.test", transport=transport, auth=_auth_from_env()
    ) as client:
        await client.health()
    assert "authorization" not in seen[-1].headers


async def test_watch_decodes_server_sent_event_frames() -> None:
    """Frames are separated by a blank line and comments are ignored."""
    body = (
        ": keep-alive\n"
        "id: 1\nevent: seat_update\ndata: {\"seat\": \"causal_scientist\"}\n\n"
        "id: 2\nevent: brief_update\ndata: {\"version\": 2}\n\n"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Last-Event-ID"] == "0"
        return httpx.Response(
            200,
            content=body,
            headers={"content-type": "text/event-stream"},
        )

    client = CLIClient()
    client._client = httpx.AsyncClient(
        base_url="http://localhost:8000",
        transport=httpx.MockTransport(handler),
    )
    try:
        frames = [frame async for frame in client.watch("t", last_event_id="0")]
    finally:
        await client.aclose()
    assert [frame["id"] for frame in frames] == ["1", "2"]
    assert [frame["event"] for frame in frames] == ["seat_update", "brief_update"]
    assert json.loads(frames[0]["data"])["seat"] == "causal_scientist"
