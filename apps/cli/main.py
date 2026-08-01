from __future__ import annotations

import argparse


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="poliscope")
    subparsers = parser.add_subparsers(dest="command")

    start = subparsers.add_parser("start")
    start.add_argument("--contract", required=True)

    confirm = subparsers.add_parser("confirm-claims")
    confirm.add_argument("--task-id", required=True)
    confirm.add_argument("--claim-ids", nargs="+", required=True)

    status = subparsers.add_parser("status")
    status.add_argument("--task-id", required=True)

    watch = subparsers.add_parser("watch")
    watch.add_argument("--task-id", required=True)
    watch.add_argument("--last-event-id", default=None)

    export = subparsers.add_parser("export")
    export.add_argument("--task-id", required=True)
    export.add_argument("--format", choices=["markdown", "json"], default="markdown")

    return parser


def main() -> None:
    parser = build_parser()
    parser.parse_args()


if __name__ == "__main__":
    main()
