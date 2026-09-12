# The seven-round council protocol

Seven rounds, in this order, no skipping and no reordering. Rounds 1–5 are
evidence work; the researcher checkpoint sits between 5 and 6; rounds 6–7 turn
evidence into a defensible answer. Each round names the seats' input, the
seats' output, and what the orchestrator is allowed to pass forward.

A round that produced nothing new is still recorded as a round. Do not collapse
two rounds into one because the answer already looks clear — the independent
precommitment in round 1 and the independent rejudgment in round 7 are the two
points where a seat's mind can change, and both are worthless if they are not
separated from the exchange in between.

---

## Round 1 — Independent precommitment

**Input:** the research question, the confirmed atomic claims. Nothing else.

**Each seat returns:**
- `judgment` — its current position on the claims, in its own voice
- `confidence` — 0.0–1.0, per claim where claims differ
- `blindspots` — what it thinks nobody has looked at yet
- `update_condition` — the specific observation that would move it

**Orchestrator:** collect all seven, write them to `council.md`, and **seal**
them. A sealed precommitment is never edited afterwards. Sealing is what makes
round 7 meaningful: the record shows what each seat believed before it saw
anyone else, so a changed position can be attributed to evidence rather than to
the loudest voice in the room.

## Round 2 — Professional acquisition

**Input:** same as round 1 — still blind.

**Each seat returns:** the sources it found and its own `StudyFinding` objects
for them — source id, locator (section/page), exact quote, design, dataset key.

**Orchestrator:** merge the returned sources into one list, deduplicating by
DOI and URL so one paper retrieved by four seats is one source, not four. That
merged list is the **shared retrieval cache** — it is also the *only* set of
source ids any later round may cite. A finding anchored to a source that is not
on this list does not exist.

## Round 3 — Evidence exchange

**Input:** the merged source list, plus each seat's own findings. Still no
judgments from other seats.

**Each seat returns:** a structured action set over the shared evidence —
`SUPPORT`, `REFUTES`, `QUALIFIES`, `CONFOUNDS`, `MEDIATES`, `MODERATES` and so
on, each edge carrying the finding it rests on.

**Orchestrator:** assemble the first version of the evidence graph. At this
point the graph holds what the evidence says, not what anyone concludes from
it.

## Round 4 — Cross-examination

**Input:** the evidence graph, plus each seat's published evidence projection —
who published what, with source ids and levels. Still no seat's private
reasoning.

**Each seat returns:** `CHALLENGE` actions aimed at another seat's finding,
edge, or inference — with the reason. A challenged seat answers with exactly
one of `DEFEND`, `REVISE`, `NARROW`, `WITHDRAW`, or `DISSENT`.

**Orchestrator:** record every challenge and every response verbatim. A
challenge that is not answered is not a challenge that went away — it becomes a
blindspot in round 5, and if it survives to the end it becomes a dissent
certificate.

## Round 5 — Blindspot bounty

**Input:** the evidence graph, every unresolved challenge, and round 1's
blindspot lists.

**Each seat returns:** the gaps it is willing to own, plus any gap it thinks
the council is collectively blind to. Each gap gets three scores, 1–5:

- **impact** — how much the missing evidence would move the conclusion
- **uncertainty** — how little is currently known about it
- **investigability** — whether a real study could settle it

**Orchestrator:** deduplicate the gaps, rank by impact × uncertainty ×
investigability, and publish the ranked list. Not all of it is researchable in
this run. Say so rather than quietly dropping the low-ranked ones.

## ↓ Researcher checkpoint (between rounds 5 and 6)

Pause here and show the researcher: where each seat stood after five rounds,
what is still contested, and the ranked blindspot list. Ask for a directional
note, or an explicit pass.

A directional note may steer **what the council discusses next** — which open
conflict or boundary condition to spend round 6 on. It may not:

- count as a vote, or be aggregated with anything into a truth score
- become evidence for or against any claim
- enter a dissent certificate or a consensus condition

Inject it into round 6 as a clearly attributed block — `[researcher's
directional note, not a scientific judgment]` — and record in `council.md`
that it was given and what it said.

## Round 6 — Joint modeling

**Input:** the evidence graph, the ranked blindspots, the researcher's
directional note (if any).

**Each seat returns:** its position on a joint model of the answer — which
claims hold, under what conditions, and what would break each one.

**Orchestrator:** build the **conditional consensus**. For every claim, one of:
`supported`, `contested`, `insufficient`. A claim is `contested` whenever the
evidence graph holds a live `REFUTES` or `CONFOUNDS` edge against it, and a
contested claim keeps the disagreement visible instead of being rounded to a
majority position. **There is no vote count anywhere in this step.** A claim
cannot leave round 6 as `supported` without at least one stated condition
under which it would fail.

## Round 7 — Final independent rejudgment

**Input:** everything in the shared frontier — the graph, the consensus draft,
the blindspot list — plus each seat's own sealed round-1 precommitment.

**Each seat returns:** its final position, its confidence, and — where the
position moved — **which piece of evidence moved it**, citing the finding.

**Orchestrator:** record every seat's round 1 → round 7 movement. A seat whose
position did not move says so explicitly; held ground after cross-examination
is a result, not a failure to participate.

Then close:

1. Every claim the evidence graph refutes, and every disagreement that survived
   rounds 4–7, becomes a **dissent certificate** naming the seat that holds it,
   the position it holds, what it is against, and the evidence that would
   resolve it.
2. Nothing is deleted. Superseded positions, refuted findings, and quarantined
   items stay in the artifacts with the reason they were set aside.
3. Run `scripts/check_evidence.mjs` over `evidence.json`. Fix what it reports.
   It checks the mechanical part of the evidence gate — see
   `references/evidence-gate.md`. It cannot check whether a judgment is any
   good, and passing it is not a claim that the research is correct.

---

## Structured actions

Seats never speak in prose during rounds 3–7. They emit one of:

| Action | Meaning |
|---|---|
| `PROPOSE` | assert a new claim or model |
| `SUPPORT` | add evidence for an existing claim |
| `CHALLENGE` | attack a claim, a finding, an edge, or an inference |
| `QUALIFY` | accept a claim only within a stated boundary |
| `FORK` | split a disputed claim into two that can be tested separately |
| `REQUEST` | ask for a specific piece of evidence |
| `REVISE` | change your own earlier position |
| `DISSENT` | record a disagreement that is not going to be resolved here |

A challenged seat answers with exactly one of `DEFEND`, `REVISE`, `NARROW`,
`WITHDRAW`, `DISSENT`.

## What may never happen

- **No majority vote decides a scientific question.** Support counts are
  bookkeeping, never verdicts.
- **No seat's private reasoning is quoted, paraphrased, or summarised into
  another seat's input**, in any round.
- **No finding enters the graph without a source and a locator.** "Several
  studies show" is not a finding.
- **No claim is deleted for being inconvenient.** Refuted is a status, not a
  deletion.
- **No round is simulated.** If a seat failed to return, say which seat and
  which round, and mark the run as completed with gaps.
