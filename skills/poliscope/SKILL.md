---
name: poliscope
description: Run an auditable Poliscope research task on a contested empirical question — seven role-specialised scientist subagents deliberate over seven rounds and write an evidence map with blindspots, preserved dissent, and a falsifiable final paper into the user's repository. Runs entirely inside this agent (no account, no server, no Python). Use when the user asks to research a contested empirical question, wants blindspots and preserved dissent instead of a single summarised answer, or mentions Poliscope, a Research Contract, or the 7-scientist council.
allowed-tools: Task, Agent, SendMessage, WebSearch, WebFetch, Read, Write, Edit, Glob, Grep, Bash(node ${CLAUDE_SKILL_DIR}/scripts/check_evidence.mjs *)
---

# Poliscope

Seven AI scientists review a contested empirical question and produce an
evidence map where conclusions sit beside limitations, every claim traces to a
source, and no dissent is deleted.

**This skill runs inside your own agent.** There is no Poliscope account, no
server to reach, and no Python to install. The seven seats are seven subagents
you spawn; their model access, their search, and their tools are yours. The
only file you need from this skill besides this one is
`scripts/check_evidence.mjs`, which is Node and runs anywhere you were
installed from.

Read these before you start, and read the ones you need again as you get to
them — do not try to hold the whole protocol in your head at once:

- `references/seats.md` — the seven role specifications and their isolation rules
- `references/protocol.md` — the seven rounds, what each produces, the structured actions
- `references/evidence-gate.md` — admission rules, evidence levels, what must never be written as a result
- `references/outputs.md` — the artifact layout and the `evidence.json` schema

## What this is not

It is not a summariser, and it is not a debate club. A run that produces a tidy
answer without a single blindspot, a single unresolved challenge, or a single
dissent certificate has almost certainly failed rather than succeeded. Do not
smooth disagreement into a conclusion — the disagreement is the product.

It is also **not** the DB-backed Poliscope service. The server build enforces
the evidence gate in database privileges with a single-writer projector and an
append-only ledger; this harness build enforces it with the protocol in
`references/` and the script in `scripts/`. That is weaker, and `paper.md`
must say so when it states its own limitations. Do not describe a harness run
as if it had the ledger.

## Workflow

### 1. Get the question and nothing else

From the user's request, take: the research question, target
populations/regions/languages, a date range if implied, which evidence
priorities matter, whether preprints count, and any DOIs or material they
handed you. Do not go looking for more — do not scan their repository, do not
infer context they did not mention. Send only the question, the contract, and
what they explicitly gave you.

### 2. Draft a Research Contract and get it confirmed

Write `contract.md` with the question, scope, populations, date range, evidence
priorities, inclusion rules for preprints, and the budget you intend to spend
(how many sources, roughly, and how deep). Show it to the user and get an
explicit yes or a correction **before** spawning any seat.

This step is not optional. A contract the user never saw is the skill inventing
its own scope, and the whole product claim is researcher-directed research.

### 3. Spawn the seven seats

Spawn all seven **at once**, in one message, one subagent per seat, each with:

- its role instruction and expertise from `references/seats.md`
- the research question and the confirmed claims from `contract.md`
- the round-1 output shape (judgment, confidence, blindspots, update condition)
- the isolation rule: it sees no other seat's output this round, and it must
  not ask for one

Keep the agent ids. Do not respawn seats between rounds — a seat's private
context *is* its private memory, and re-spawning it each round would erase
exactly the continuity that makes the last round meaningful.

If your harness cannot spawn subagents at all, stop and tell the user: a
single context cannot hold seven independent seats, and one voice wearing seven
hats is not this protocol. Offer to run the question as an ordinary research
task instead.

### 4. Run the seven rounds

Follow `references/protocol.md`. Rounds 1 and 2 are blind — no seat sees
another's judgment or findings. From round 3 on, each seat receives only the
shared frontier you assemble (the merged source list, the evidence graph, the
blindspot list) plus its own memory, never another seat's raw output.

Between rounds, write state to files rather than carrying it in your own
context — the round-1 precommitments especially, which must be sealed before
round 3 and read back unchanged in round 7.

### 5. Stop at the researcher checkpoint

After round 5 and before round 6, pause and show the user: where each seat
stands, what is still contested, and the ranked blindspot list. Ask for a
directional note or an explicit pass.

A note may steer what round 6 discusses. It may not count as a vote, become
evidence for or against a claim, or enter a consensus condition. Quote it in
`council.md` labelled as not a scientific judgment.

### 6. Close the run

Round 6 builds the conditional consensus — `supported`, `contested`, or
`insufficient` per claim, with no vote count anywhere. Round 7 is each seat's
final independent position, next to its own sealed precommitment, with what
moved it.

Then write the artifacts in `references/outputs.md`, and run the gate:

```bash
node ${CLAUDE_SKILL_DIR}/scripts/check_evidence.mjs docs/poliscope/<slug>/
```

Fix every `FAIL` before you finish. `WARN` lines are for the user to see, not
for you to hide — carry them into the limitations list if they survive.

### 7. Report, and be honest about the edges

Tell the user, in your own words: what the evidence supports and under what
conditions, what it does not settle, the strongest counterexample, the top
blindspots, and every gap in the run itself — a seat that failed to return, a
budget you ran past, evidence you could not get past a paywall. A blank is
never "no problem."

Point them at the output directory. `paper.md` is the assembled paper;
`evidence.md` is the map to browse; `dissent.md` is the part most people skip
and should not.

## Hard constraints

- **No seat may see another seat's private reasoning**, in any round. Structured
  output crosses; deliberation does not.
- **No majority vote decides a scientific question.** Support counts are
  bookkeeping, never verdicts. The output is a conditional consensus plus
  named dissent certificates.
- **No finding enters the graph without a source, a locator, and an exact
  quote.** "Several studies show" is not a finding.
- **Nothing is deleted.** Refuted, superseded, and quarantined items stay in
  the artifacts with the reason they were set aside.
- **Correlation never upgrades to causation.** A supported causal claim needs
  a design that can identify an effect, quoted from full text.
- **Never present in-progress or unaudited content as a formal conclusion**,
  and never let `paper.md` assert anything that is not already in the graph,
  the consensus, or a dissent certificate.
- **Do not fetch or store personal data, and do not bypass paywalls or access
  controls.** Abstract-only means Level B; say so rather than implying you read
  the paper.

## Safety

- This is a research aid, not medical or clinical advice, and not a diagnostic
  tool. Say so in any report touching mental health.
- Model confidence does not replace statistical uncertainty or expert judgment.
- No subagent in this protocol may be given the user's credentials, private
  files, or anything the contract did not name.
