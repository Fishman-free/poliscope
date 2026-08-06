---
name: poliscope
description: Run an auditable Poliscope research task for a computational-social-science controversy (digital behavior, social media, mental health). Use when the user asks to research a contested empirical question, wants an evidence map with blindspots and preserved dissent instead of a single summarized answer, or explicitly mentions Poliscope, a Research Contract, or the 7-scientist council.
allowed-tools: Bash(poliscope *), Bash(python ${CLAUDE_SKILL_DIR}/scripts/new_contract.py *)
---

# Poliscope

Poliscope is a thin adapter over the `poliscope` CLI, which is itself a thin
HTTP client over the Research Service API. This skill never imports Poliscope's
`packages/`, never calls a model or a paper source directly, never writes to
the database or Evidence Graph, and never skips claim confirmation or the
Evidence Gate. It only talks to the same CLI/API contract a human operator
would use by hand (design spec `docs/superpowers/specs/2026-07-31-poliscope-design.md`
section 8.7). If you are tempted to shortcut any of this to "just get an
answer faster," don't -- that would make this a second, divergent Poliscope
implementation, which is exactly what the design forbids.

**Known limitation, stated plainly rather than silently assumed away:** the
`allowed-tools` line above only pre-approves those two command shapes so you
are not asked to confirm every single invocation -- it does not technically
prevent you from calling other tools. Do not use it as an excuse to reach for
`packages/`, a database client, or a model API directly during a Poliscope
task; the constraint is enforced by your own discipline here, not by the
platform.

## Getting the `poliscope` CLI without cloning

If `poliscope` is not already on PATH, the fastest path is a zero-install run
via `uv` (the Python ecosystem's equivalent of `npx`) -- no clone, no venv to
manage:

```bash
uvx --from "git+https://github.com/Fishman-free/poliscope.git" poliscope --help
```

Every `poliscope` subcommand in this file works the same way, e.g.
`uvx --from "git+https://github.com/Fishman-free/poliscope.git" poliscope health`.
Prefer a normal `pip install`/local checkout instead if you expect to run many
commands in one session -- `uvx` resolves the environment on every
invocation, which is fine for occasional calls but wasteful in a tight loop.

## Workflow

0. **Authenticate once (deployed instances only).** A local API on
   localhost needs no credentials. A deployed instance requires an account:
   `poliscope login --base-url <URL>` exchanges username/password for a
   30-day token saved under `~/.poliscope/credentials.json`, and every later
   command picks it up automatically. Non-interactive agents set
   `POLISCOPE_API_TOKEN` instead.

1. **Understand the question.** From the user's request, extract: the
   research question itself, target populations/regions/languages, a date
   range if implied, which `evidence_priorities` matter most (see the enum
   below), whether preprints should count, and any DOIs, BibTeX entries, or
   explicit budget the user gave you. Do not go looking for more -- do not
   scan the user's repository, do not infer materials they did not mention.
   Per design-spec 8.7, this skill sends only the question, the Contract, and
   material the user explicitly handed you.

2. **Draft a Research Contract, do not submit it yet.** Build it with the
   shared scaffolding script so the JSON shape always matches
   `packages/research/contracts.py`'s `ResearchContract` schema without you
   hand-typing it:

   ```bash
   python ${CLAUDE_SKILL_DIR}/scripts/new_contract.py \
     --question "<verbatim research question>" \
     --population "<population>" --region "<region>" --language "<lang>" \
     --evidence-priority CAUSAL_OR_REVERSE_CAUSAL --evidence-priority MEASUREMENT \
     --doi "<doi if the user gave one>" \
     --output poliscope_contract.json
   ```

   `--evidence-priority` accepts `CORRELATION`, `CAUSAL_OR_REVERSE_CAUSAL`,
   `MEASUREMENT`, `REPLICATION`, `BOUNDARY`, `MECHANISM`, or
   `NULL_OR_COUNTEREXAMPLE` (repeatable). Omitted budget flags default to a
   modest 60-minute / 50-tool-call / 20-source run; raise them only if the
   user asks for a deeper pass.

   **Known, honest gap:** `POST /api/tasks/{task_id}/papers/upload` exists and
   really parses the PDF into a `StudyFinding`, but it needs a task id to
   attach the object to, so it can only be called *after* this script creates
   the task -- this script itself always leaves `pdf_object_ids` empty in the
   initial contract. There is also no `apps/cli` command for the upload step
   yet (only the raw HTTP endpoint). If a user wants to attach a PDF, tell
   them the task must exist first and the file has to be uploaded via that
   endpoint directly (e.g. `curl -F file=@paper.pdf ...`) rather than through
   this skill, which does not upload files on its own initiative (design spec
   8.7).

3. **Show the drafted contract to the user and get explicit confirmation**
   before creating the task. This is not optional -- a Research Contract that
   was never shown to the user is not meaningfully "user-directed research,"
   it's a skill inventing its own scope.

4. **Create the task and confirm claims** -- two separate, deliberate steps,
   because claim confirmation is Poliscope's own gate against research drift:

   ```bash
   poliscope start --contract poliscope_contract.json --json
   # -> prints suggested_claims; show them to the user, ask which to keep
   poliscope confirm-claims --task-id <task_id> --claim-ids <id1> <id2> ...
   ```

5. **Report progress**, either a single snapshot or a live follow:

   ```bash
   poliscope status --task-id <task_id> --json
   poliscope watch --task-id <task_id>          # streams until the run ends
   poliscope pause --task-id <task_id>          # only works while still QUEUED
   poliscope resume --task-id <task_id>
   ```

   Summarize status in your own words for the user -- structured actions,
   evidence used, challenges and responses, conclusions and confidence
   changes. Never surface a seat's private chain-of-thought; Poliscope's own
   API does not expose it, so there is nothing to accidentally leak here, but
   do not paraphrase your own reasoning about the task as if it were the
   council's either.

6. **Export to a location the user names**, never a silent default:

   ```bash
   poliscope export --task-id <task_id> --format markdown --output <user-chosen-path>
   ```

   If the run reports `COMPLETED_WITH_GAPS` (for example, no model vendor
   configured, or a budget ran out), say so plainly in your summary --
   `has_gaps` and `limitations` in the exported brief exist precisely so this
   is never silently smoothed over.

7. **Write the full results into the user's project (round-4 skill output).**
   After a task finishes, put the evidence map, the council record, and each
   scientist's position into the project itself so the user can browse them
   in their repository, not only in the web workbench:

   ```bash
   poliscope export-docs --task-id <task_id> --output docs/poliscope
   ```

   This creates `docs/poliscope/{task-slug}/` with `README.md` (index),
   `brief.md` (server-rendered research brief), `evidence.md` (evidence map:
   nodes and edges with paper/cluster counts), `council.md` (precommitments,
   challenges, final judgments, evolution timeline), and `scientists/` (one
   file per seat). Every fact comes from the API snapshot -- nothing is
   re-serialised or invented by the CLI. Point the user at the directory in
   your final summary.

## Hard constraints (design spec 8.7, non-negotiable)

- Never call a model provider or a paper/data source directly from this skill.
- Never write to Poliscope's database or Evidence Graph directly.
- Never bypass atomic-claim confirmation or the Evidence Gate.
- Never present unaudited or in-progress content as a formal conclusion.
- Default to sending only the research question, the Contract, and materials
  the user explicitly handed you -- no unprompted repo scanning, no file
  uploads, and keep PDFs, signed URLs, local absolute paths, and any model
  chain-of-thought out of logs and exports.
- Long tasks return a `task_id` on purpose: if the user leaves and comes back
  later (a new conversation, a different machine), `status`/`watch`/`export`
  against that same `task_id` picks the task back up -- you do not need to
  restart it.

## Safety

- This is a research aid, not medical or clinical advice or a diagnostic tool.
- Model confidence does not replace statistical uncertainty or expert judgment.
- Every command needs a reachable Poliscope API (`poliscope health` checks
  this first if you are unsure one is running).
- A deployed instance requires an account: run `poliscope login
  --base-url <URL>` once (or set `POLISCOPE_API_TOKEN`), and every command
  sends the session token automatically. Leave both unset for a local,
  un-gated API.
