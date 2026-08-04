"""Command line surface for Poliscope.

Every subcommand is a thin adapter over one API route, so the CLI and the web
workspace observe the same task state and the same Evidence Gate. Formatting and
argument parsing belong here; research logic does not.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from apps.cli import exit_codes
from apps.cli.client import (
    DEFAULT_BASE_URL,
    APIError,
    APIUnreachable,
    CLIClient,
)

EPILOG = """\
examples:
  poliscope start --contract research.json
  poliscope status --task-id 7f3a... --json
  poliscope pause --task-id 7f3a...
  poliscope resume --task-id 7f3a...
  poliscope watch --task-id 7f3a... --last-event-id 41
  poliscope export --task-id 7f3a... --format markdown --output brief.md
  poliscope council-preview --task-id 7f3a...
  poliscope council-guidance --task-id 7f3a... --text "focus on cross-cultural scope"
  poliscope council-guidance --task-id 7f3a... --text ""

Every command needs a running API. Start one with:
  uvicorn apps.api.main:app --reload
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="poliscope",
        description=(
            "Auditable controversy evidence mapping for computational "
            "social science."
        ),
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--base-url",
        default=DEFAULT_BASE_URL,
        metavar="URL",
        help=f"address of the Poliscope API (default: {DEFAULT_BASE_URL})",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="emit raw JSON instead of a human readable summary",
    )
    subparsers = parser.add_subparsers(dest="command", metavar="COMMAND")

    health = subparsers.add_parser(
        "health",
        help="check that the API and its database are reachable",
    )
    health.set_defaults(handler=_cmd_health)

    start = subparsers.add_parser(
        "start",
        help="create a research task from a contract file",
        description=(
            "Read a JSON contract holding question, scope, budget and "
            "user_evidence, then create the task. The task stops at claim "
            "confirmation; it does not start researching until you run "
            "confirm-claims."
        ),
    )
    start.add_argument(
        "--contract",
        required=True,
        metavar="PATH",
        help="path to the JSON research contract",
    )
    start.set_defaults(handler=_cmd_start)

    confirm = subparsers.add_parser(
        "confirm-claims",
        help="confirm the atomic claims a task will investigate",
        description=(
            "Confirms which suggested atomic claims the council will work on "
            "and queues the task. Claims cannot be confirmed twice."
        ),
    )
    confirm.add_argument("--task-id", required=True, metavar="UUID")
    confirm.add_argument(
        "--claim-ids",
        nargs="+",
        required=True,
        metavar="UUID",
        help="one or more claim ids returned by start",
    )
    confirm.set_defaults(handler=_cmd_confirm_claims)

    status = subparsers.add_parser(
        "status",
        help="print the current workspace snapshot for a task",
    )
    status.add_argument("--task-id", required=True, metavar="UUID")
    status.set_defaults(handler=_cmd_status)

    pause = subparsers.add_parser(
        "pause",
        help="keep a queued task from being claimed until it is resumed",
        description=(
            "Moves a QUEUED task to PAUSED. Only a task still waiting for a "
            "worker can be paused; one already running finishes its current "
            "pass regardless."
        ),
    )
    pause.add_argument("--task-id", required=True, metavar="UUID")
    pause.set_defaults(handler=_cmd_pause)

    resume = subparsers.add_parser(
        "resume",
        help="move a paused task back to the run queue",
    )
    resume.add_argument("--task-id", required=True, metavar="UUID")
    resume.set_defaults(handler=_cmd_resume)

    watch = subparsers.add_parser(
        "watch",
        help="follow a task's event stream until it ends",
        description=(
            "Streams workspace events. Pass --last-event-id to resume without "
            "replaying events you have already seen."
        ),
    )
    watch.add_argument("--task-id", required=True, metavar="UUID")
    watch.add_argument(
        "--last-event-id",
        default=None,
        metavar="ID",
        help="resume after this event id instead of from the beginning",
    )
    watch.set_defaults(handler=_cmd_watch)

    export = subparsers.add_parser(
        "export",
        help="export the research brief and audit trail",
    )
    export.add_argument("--task-id", required=True, metavar="UUID")
    export.add_argument(
        "--format",
        choices=["markdown", "json"],
        default="markdown",
        dest="export_format",
    )
    export.add_argument(
        "--output",
        default=None,
        metavar="PATH",
        help="write to this file instead of standard output",
    )
    export.set_defaults(handler=_cmd_export)

    council_preview = subparsers.add_parser(
        "council-preview",
        help="show the 7 seats' positions while a task awaits guidance",
        description=(
            "Read-only view of where the 7 scientists stood at the end of "
            "BLINDSPOT_BOUNTY, while the task is halted at "
            "AWAITING_COUNCIL_INPUT. This is advisory context for the "
            "researcher, not a vote -- CLAUDE.md 4/8."
        ),
    )
    council_preview.add_argument("--task-id", required=True, metavar="UUID")
    council_preview.set_defaults(handler=_cmd_council_preview)

    council_guidance = subparsers.add_parser(
        "council-guidance",
        help="submit (or decline) directional guidance before JOINT_MODELING",
        description=(
            "Submits the researcher's advisory steer and lets the worker "
            "resume the council. Pass --text \"\" to decline -- declining is "
            "a deliberate, honest answer, not a missing one, and it never "
            "changes evidence adjudication (CLAUDE.md 4/8)."
        ),
    )
    council_guidance.add_argument("--task-id", required=True, metavar="UUID")
    council_guidance.add_argument(
        "--text",
        dest="guidance_text",
        required=True,
        metavar="TEXT",
        help='directional note, or "" to continue without intervention',
    )
    council_guidance.set_defaults(handler=_cmd_council_guidance)

    return parser


async def _cmd_health(client: CLIClient, args: argparse.Namespace) -> int:
    payload = await client.health()
    if args.as_json:
        _print_json(payload)
    else:
        print(f"api        {payload.get('status', 'unknown')}")
        print(f"database   {payload.get('database', 'unknown')}")
    return exit_codes.OK


async def _cmd_start(client: CLIClient, args: argparse.Namespace) -> int:
    contract_path = Path(args.contract)
    try:
        contract = json.loads(contract_path.read_text(encoding="utf-8"))
    except OSError as error:
        print(f"poliscope: cannot read {contract_path}: {error}", file=sys.stderr)
        return exit_codes.FAILED
    except json.JSONDecodeError as error:
        print(
            f"poliscope: {contract_path} is not valid JSON: {error}",
            file=sys.stderr,
        )
        return exit_codes.FAILED
    payload = await client.create_task(contract)
    if args.as_json:
        _print_json(payload)
        return exit_codes.OK
    print(f"task       {payload.get('task_id', payload.get('id', '?'))}")
    print(f"status     {payload.get('status', '?')}")
    claims = payload.get("suggested_claims") or ()
    if claims:
        print(f"\n{len(claims)} suggested atomic claims. Confirm the ones to keep:")
        for claim in claims:
            print(f"  {claim.get('id', '?')}  {claim.get('statement', '')}")
    return exit_codes.OK


async def _cmd_confirm_claims(client: CLIClient, args: argparse.Namespace) -> int:
    payload = await client.confirm_claims(args.task_id, args.claim_ids)
    if args.as_json:
        _print_json(payload)
    else:
        print(f"status     {payload.get('status', '?')}")
        print(f"confirmed  {len(args.claim_ids)} claims")
    return exit_codes.OK


async def _cmd_status(client: CLIClient, args: argparse.Namespace) -> int:
    snapshot = await client.workspace(args.task_id)
    if args.as_json:
        _print_json(snapshot)
        return exit_codes.OK
    task = snapshot.get("task") or {}
    print(f"task       {task.get('task_id', args.task_id)}")
    print(f"status     {task.get('status', '?')}")
    print(f"version    {snapshot.get('workspace_version', '?')}")
    print(
        f"evidence   {snapshot.get('paper_count', 0)} papers / "
        f"{snapshot.get('independent_cluster_count', 0)} independent clusters"
    )
    print(f"blindspots {len(snapshot.get('blindspots') or ())}")
    print(f"dissents   {len(snapshot.get('dissents') or ())}")
    notice = snapshot.get("safety_notice") or {}
    if notice:
        print(f"\n{notice.get('medical_disclaimer', '')}")
    return exit_codes.OK


async def _cmd_pause(client: CLIClient, args: argparse.Namespace) -> int:
    payload = await client.pause(args.task_id)
    if args.as_json:
        _print_json(payload)
    else:
        print(f"status     {payload.get('status', '?')}")
    return exit_codes.OK


async def _cmd_resume(client: CLIClient, args: argparse.Namespace) -> int:
    payload = await client.resume(args.task_id)
    if args.as_json:
        _print_json(payload)
    else:
        print(f"status     {payload.get('status', '?')}")
    return exit_codes.OK


async def _cmd_watch(client: CLIClient, args: argparse.Namespace) -> int:
    async for frame in client.watch(args.task_id, args.last_event_id):
        data = frame.get("data", "")
        if args.as_json:
            print(data, flush=True)
            continue
        event_id = frame.get("id", "?")
        # The kind is in the body, not on an `event:` line. Reading it from the
        # frame header printed "message" for everything once the server stopped
        # typing its frames -- see apps/api/schemas.py for why it stopped.
        try:
            kind = str(json.loads(data)["kind"])
        except (ValueError, KeyError, TypeError):
            kind = "unknown"
        print(f"[{event_id}] {kind}", flush=True)
    return exit_codes.OK


async def _cmd_council_preview(client: CLIClient, args: argparse.Namespace) -> int:
    payload = await client.council_preview(args.task_id)
    if args.as_json:
        _print_json(payload)
        return exit_codes.OK
    print(f"task       {payload.get('task_id', args.task_id)}")
    print(f"status     {payload.get('status', '?')}")
    seats = payload.get("seats") or ()
    for seat in seats:
        precommitment = seat.get("precommitment") or {}
        challenges = seat.get("challenges_raised") or ()
        print(f"\n{seat.get('seat', '?')}")
        if precommitment:
            print(
                f"  confidence        {precommitment.get('confidence', '?')}"
            )
            print(
                f"  update_condition  {precommitment.get('update_condition', '?')}"
            )
        print(f"  challenges_raised {len(challenges)}")
        if seat.get("unavailable_phases"):
            print(f"  unavailable       {list(seat['unavailable_phases'])}")
    return exit_codes.OK


async def _cmd_council_guidance(client: CLIClient, args: argparse.Namespace) -> int:
    payload = await client.council_guidance(args.task_id, args.guidance_text)
    if args.as_json:
        _print_json(payload)
    else:
        print(f"status     {payload.get('status', '?')}")
        if args.guidance_text:
            print("guidance   submitted")
        else:
            print("guidance   none (continuing without intervention)")
    return exit_codes.OK


async def _cmd_export(client: CLIClient, args: argparse.Namespace) -> int:
    # The server already renders both formats, so the CLI writes what it is
    # given rather than re-serialising it. Reformatting here would let the
    # exported file and the API's own answer drift apart.
    rendered = await client.export(args.task_id, args.export_format)
    if args.output is None:
        print(rendered)
        return exit_codes.OK
    output_path = Path(args.output)
    try:
        output_path.write_text(rendered, encoding="utf-8")
    except OSError as error:
        print(f"poliscope: cannot write {output_path}: {error}", file=sys.stderr)
        return exit_codes.FAILED
    print(f"wrote {output_path}")
    return exit_codes.OK


def _print_json(payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


async def _run(args: argparse.Namespace) -> int:
    async with CLIClient(args.base_url) as client:
        handler = args.handler
        result: int = await handler(client, args)
        return result


def main(argv: Sequence[str] | None = None) -> int:
    """Parse ``argv`` and run one subcommand. Returns a process exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "handler", None) is None:
        parser.print_help()
        return exit_codes.USAGE
    try:
        return asyncio.run(_run(args))
    except APIUnreachable as error:
        print(f"poliscope: {error}", file=sys.stderr)
        return exit_codes.UNREACHABLE
    except APIError as error:
        print(
            f"poliscope: request rejected ({error.status_code}): {error.detail}",
            file=sys.stderr,
        )
        return exit_codes.REQUEST_REJECTED
    except KeyboardInterrupt:
        return exit_codes.INTERRUPTED


if __name__ == "__main__":
    sys.exit(main())
