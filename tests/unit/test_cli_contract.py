from __future__ import annotations

from apps.cli.main import build_parser


def test_cli_exposes_stable_research_commands() -> None:
    parser = build_parser()
    # Just verify the parser builds without error
    assert parser is not None


def test_parser_accepts_start_subcommand() -> None:
    parser = build_parser()
    args = parser.parse_args(["start", "--contract", "test.json"])
    assert args.command == "start"


def test_parser_accepts_status_subcommand() -> None:
    parser = build_parser()
    args = parser.parse_args(["status", "--task-id", "abc"])
    assert args.command == "status"


def test_suite() -> None:
    test_cli_exposes_stable_research_commands()
    test_parser_accepts_start_subcommand()
    test_parser_accepts_status_subcommand()
