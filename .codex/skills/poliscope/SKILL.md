---
name: poliscope
description: Run an auditable Poliscope research task for a computational-social-science controversy (digital behavior, social media, mental health). Use when the user asks to research a contested empirical question, wants an evidence map with blindspots and preserved dissent instead of a single summarized answer, or explicitly mentions Poliscope, a Research Contract, or the 7-scientist council.
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

This file is kept content-identical to
`.claude/skills/poliscope/SKILL.md` (the Claude Code copy of this same
skill), aside from frontmatter fields specific to each tool -- the point is
one shared workflow, not two that can drift apart.

## Workflow

1. **Understand the question.** From the user's request, extract: the
   research question itself, target populations/regions/languages, a date
   range if implied, which `evidence_priorities` matter most (see the enum
   below), whether preprints should count, and any DOIs, BibTeX entries, or
   explicit budget the user gave you. Do not go looking for more -- do not
   scan the user's repository, do not infer materials they did not mention.
   Per design-spec 8.7, this skill sends only the question, the Contract, and
   material the user explicitly handed you.

2. **Draft a Research Contract, do not submit it yet.** Build it with the
   shared scaffolding script, kept next to this file at
   `scripts/new_contract.py`, so the JSON shape always matches
   `packages/research/contracts.py`'s `ResearchContract` schema without you
   hand-typing it:

   ```bash
   python scripts/new_contract.py \
     --question "<verbatim research question>" \
     --population "<population>" --region "<region>" --language "<lang>" \
     --evidence-priority CAUSAL_OR_REVERSE_CAUSAL --evidence-priority MEASUREMENT \
     --doi "<doi if the user gave one>" \
     --output poliscope_contract.json
   ```

   (Resolve `scripts/new_contract.py` relative to this SKILL.md file's own
   directory, wherever this skill was installed.)

   `--evidence-priority` accepts `CORRELATION`, `CAUSAL_OR_REVERSE_CAUSAL`,
   `MEASUREMENT`, `REPLICATION`, `BOUNDARY`, `MECHANISM`, or
   `NULL_OR_COUNTEREXAMPLE` (repeatable). Omitted budget flags default to a
   modest 60-minute / 50-tool-call / 20-source run; raise them only if the
   user asks for a deeper pass.

   **Known, honest gap:** there is currently no upload endpoint that turns a
   local PDF into the `pdf_object_ids` a contract accepts, so this script
   always leaves that field empty. If a user wants to attach a PDF, tell them
   this is not supported yet rather than pretending to handle it.

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
