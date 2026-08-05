"""Command line surface for Poliscope.

Every subcommand is a thin adapter over one API route, so the CLI and the web
workspace observe the same task state and the same Evidence Gate. Formatting and
argument parsing belong here; research logic does not.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import getpass
import json
import os
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

# One bearer token per base URL, so a single machine can talk to a local API
# and several deployed instances without re-logging in. The file is plain
# JSON; the token expires server-side after 30 days, so a leaked copy is a
# time-bounded secret rather than a password.
CREDENTIALS_FILE = Path.home() / ".poliscope" / "credentials.json"

EPILOG = """\
examples:
  poliscope login --username alice
  poliscope register --username alice
  poliscope logout
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

    login = subparsers.add_parser(
        "login",
        help="log in and remember the session token for this base URL",
        description=(
            "Exchanges username/password for a 30-day bearer token and stores "
            "it in ~/.poliscope/credentials.json; every later command sends it "
            "automatically. Prefer flags when running non-interactively."
        ),
    )
    login.add_argument("--username", default=None, metavar="NAME")
    login.add_argument("--password", default=None, metavar="PASSWORD")
    login.set_defaults(handler=_cmd_login)

    register = subparsers.add_parser(
        "register",
        help="create an account and log in",
        description=(
            "Registers a new account and stores the session token like login "
            "does. Interactive mode asks for the password twice; --password "
            "skips the confirmation (and leaves the password in shell "
            "history -- prefer the interactive prompt)."
        ),
    )
    register.add_argument("--username", default=None, metavar="NAME")
    register.add_argument("--password", default=None, metavar="PASSWORD")
    register.set_defaults(handler=_cmd_register)

    logout = subparsers.add_parser(
        "logout",
        help="revoke the session token and forget it locally",
        description=(
            "Revokes the token for this base URL server-side and removes it "
            "from the credentials file. A token set via POLISCOPE_API_TOKEN "
            "is revoked too, but the environment variable itself cannot be "
            "unset by this command."
        ),
    )
    logout.set_defaults(handler=_cmd_logout)

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


def _load_token(base_url: str) -> str | None:
    """Bearer token for ``base_url``: ``POLISCOPE_API_TOKEN`` wins (non-interactive
    agents), then the credentials file. A corrupt or unreadable file is treated
    as "not logged in" rather than a crash -- the 401 that follows is honest
    and self-explanatory."""
    token = os.environ.get("POLISCOPE_API_TOKEN")
    if token:
        return token
    try:
        credentials = json.loads(CREDENTIALS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    token = credentials.get(base_url.rstrip("/"))
    return token if isinstance(token, str) and token else None


def _save_token(base_url: str, token: str) -> None:
    """Merge ``token`` into the credentials file, preserving other base URLs.
    chmod 0600 is best-effort -- it is meaningful on POSIX and a no-op (with
    a swallowed error) on Windows."""
    CREDENTIALS_FILE.parent.mkdir(parents=True, exist_ok=True)
    credentials: dict[str, Any] = {}
    with contextlib.suppress(OSError, json.JSONDecodeError):
        credentials = json.loads(CREDENTIALS_FILE.read_text(encoding="utf-8"))
    credentials[base_url.rstrip("/")] = token
    CREDENTIALS_FILE.write_text(
        json.dumps(credentials, indent=2), encoding="utf-8"
    )
    with contextlib.suppress(OSError):
        CREDENTIALS_FILE.chmod(0o600)


def _drop_token(base_url: str) -> None:
    with contextlib.suppress(OSError, json.JSONDecodeError):
        credentials = json.loads(CREDENTIALS_FILE.read_text(encoding="utf-8"))
        key = base_url.rstrip("/")
        if key not in credentials:
            return
        del credentials[key]
        CREDENTIALS_FILE.write_text(
            json.dumps(credentials, indent=2), encoding="utf-8"
        )
    if key not in credentials:
        return
    del credentials[key]
    CREDENTIALS_FILE.write_text(
        json.dumps(credentials, indent=2), encoding="utf-8"
    )


def _ask_username_password(
    args: argparse.Namespace, *, confirm: bool = False
) -> tuple[str, str]:
    """Username/password from --flags, falling back to an interactive prompt.

    ``confirm`` re-reads the password once (register) so a typo does not
    silently create an account whose password nobody knows.
    """
    username = args.username if args.username else input("username: ").strip()
    password = args.password
    if password is None:
        password = getpass.getpass("password: ")
        if confirm and getpass.getpass("password (again): ") != password:
            raise ValueError("passwords do not match")
    return username, password


async def _cmd_login(client: CLIClient, args: argparse.Namespace) -> int:
    username, password = _ask_username_password(args)
    result = await client.login(username, password)
    _save_token(args.base_url, result["token"])
    print(f"logged in as {result['username']}")
    print(f"token saved to {CREDENTIALS_FILE} (base URL {args.base_url.rstrip('/')})")
    return exit_codes.OK


async def _cmd_register(client: CLIClient, args: argparse.Namespace) -> int:
    try:
        username, password = _ask_username_password(args, confirm=True)
    except ValueError as error:
        print(f"poliscope: {error}", file=sys.stderr)
        return exit_codes.FAILED
    result = await client.register(username, password)
    _save_token(args.base_url, result["token"])
    print(f"registered and logged in as {result['username']}")
    print(f"token saved to {CREDENTIALS_FILE} (base URL {args.base_url.rstrip('/')})")
    return exit_codes.OK


async def _cmd_logout(client: CLIClient, args: argparse.Namespace) -> int:
    # The endpoint is idempotent: without a token it still answers 204, and a
    # token from POLISCOPE_API_TOKEN is revoked even though the variable stays.
    await client.logout()
    _drop_token(args.base_url)
    print(f"logged out; token removed from {CREDENTIALS_FILE}")
    return exit_codes.OK


async def _run(args: argparse.Namespace) -> int:
    async with CLIClient(args.base_url, token=_load_token(args.base_url)) as client:
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
    except EOFError:
        print(
            "poliscope: no interactive input available; pass --username and "
            "--password, or set POLISCOPE_API_TOKEN",
            file=sys.stderr,
        )
        return exit_codes.FAILED


if __name__ == "__main__":
    sys.exit(main())
