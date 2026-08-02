#!/usr/bin/env python3
"""Scaffold a Poliscope Research Contract JSON from CLI flags.

This is the one shared, deterministic helper both the Claude Code and the
Codex Poliscope skill call -- the thing that keeps their research logic from
forking (design spec 8.7). It does not import `packages` and does not talk to
the Poliscope API itself: it only assembles a contract file on disk that the
calling agent shows the user for confirmation, then hands to
`poliscope start --contract <path>`. Real validation happens where it always
has -- in ResearchContract's own pydantic model, behind the API -- this script
just saves the user (and the agent) from hand-typing valid JSON.

Kept byte-identical in both .claude/skills/poliscope/scripts/ and
.codex/skills/poliscope/scripts/ -- each tool discovers skills at its own
path, so there is no single location both can read at runtime; if you edit
one copy, edit the other the same way.

Usage:
    python new_contract.py --question "..." \\
        [--population P ...] [--region R ...] [--language L ...] \\
        [--date-from YYYY-MM-DD] [--date-until YYYY-MM-DD] \\
        [--evidence-priority {CORRELATION,CAUSAL_OR_REVERSE_CAUSAL,MEASUREMENT,
                               REPLICATION,BOUNDARY,MECHANISM,
                               NULL_OR_COUNTEREXAMPLE} ...] \\
        [--allow-preprints] \\
        [--wall-clock-minutes N] [--model-cost-usd X] \\
        [--tool-call-limit N] [--source-limit N] \\
        [--doi DOI ...] [--bibtex-entry ENTRY ...] \\
        [--output PATH]
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime

EVIDENCE_PRIORITIES = (
    "CORRELATION",
    "CAUSAL_OR_REVERSE_CAUSAL",
    "MEASUREMENT",
    "REPLICATION",
    "BOUNDARY",
    "MECHANISM",
    "NULL_OR_COUNTEREXAMPLE",
)


def _parse_date(value: str) -> str:
    # Just a format check -- ResearchScope itself enforces date_from <=
    # date_until server-side, no need to duplicate that here.
    datetime.strptime(value, "%Y-%m-%d")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--question",
        required=True,
        help="the research question, verbatim from the user",
    )
    parser.add_argument("--population", dest="populations", action="append", default=[])
    parser.add_argument("--region", dest="regions", action="append", default=[])
    parser.add_argument("--language", dest="languages", action="append", default=[])
    parser.add_argument("--date-from", type=_parse_date, default=None)
    parser.add_argument("--date-until", type=_parse_date, default=None)
    parser.add_argument(
        "--evidence-priority",
        dest="evidence_priorities",
        action="append",
        choices=EVIDENCE_PRIORITIES,
        default=[],
    )
    parser.add_argument("--allow-preprints", action="store_true")
    parser.add_argument("--wall-clock-minutes", type=int, default=60)
    parser.add_argument("--model-cost-usd", default="10.00")
    parser.add_argument("--tool-call-limit", type=int, default=50)
    parser.add_argument("--source-limit", type=int, default=20)
    parser.add_argument("--doi", dest="dois", action="append", default=[])
    parser.add_argument(
        "--bibtex-entry", dest="bibtex_entries", action="append", default=[]
    )
    parser.add_argument("--output", default="poliscope_contract.json")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    date_until = args.date_until or date.today().isoformat()
    contract = {
        "question": args.question,
        "scope": {
            "populations": args.populations or ["general population"],
            "regions": args.regions or ["global"],
            "languages": args.languages or ["en"],
            "date_from": args.date_from,
            "date_until": date_until,
            "evidence_priorities": args.evidence_priorities or ["CORRELATION"],
            "allow_preprints": args.allow_preprints,
        },
        "budget": {
            "wall_clock_minutes": args.wall_clock_minutes,
            "model_cost_usd": args.model_cost_usd,
            "tool_call_limit": args.tool_call_limit,
            "source_limit": args.source_limit,
        },
        # pdf_object_ids is deliberately left empty: there is no upload
        # endpoint wired up yet (a real, documented gap -- see README's
        # "已知缺口"), and this skill never uploads files on its own
        # initiative regardless (design spec 8.7).
        "user_evidence": {
            "dois": args.dois,
            "bibtex_entries": args.bibtex_entries,
            "pdf_object_ids": [],
        },
    }

    with open(args.output, "w", encoding="utf-8") as handle:
        json.dump(contract, handle, ensure_ascii=False, indent=2)
        handle.write("\n")

    print(
        f"Wrote {args.output} -- show this to the user for confirmation "
        f"before `poliscope start --contract {args.output}`."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
