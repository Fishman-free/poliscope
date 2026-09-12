# The evidence gate

Every formal claim has to survive this before it is written into `paper.md` as
a conclusion. The rules are the product, not a style guide — a run that skips
them produced a plausible essay, not research.

## Evidence levels

| Level | What is available | What it may support |
|---|---|---|
| **A** | full text, exact quote locatable | anything, subject to design |
| **B** | abstract and reliable metadata only | never a high-confidence causal conclusion on its own |
| **C** | second-hand description inside a review | never a substitute for the original study |
| **D** | web page or news lead | leads for round 2, never evidence in the graph |

A level is a property of what the run actually retrieved, not of how good the
paper is. Paywalled means "abstract only" here, whatever the paper deserves.

## Every `StudyFinding` carries

- `source_id` — a source on the merged round-2 list, not a bare citation
- `locator` — section, and page or equivalent position
- `quote` — the exact words from the source that carry the finding
- `design` — `experiment`, `quasi_experiment`, `longitudinal`, `cross_sectional`,
  `meta_analysis`, `qualitative`, or `review`
- `dataset_key` — the dataset it rests on, so two papers on one dataset are
  countable as one cluster

A finding missing any of these is not admitted. There is no "close enough"
here: the whole point of an auditable map is that a reader can go and check.

## Three-layer audit

Before a finding is admitted, the **`evidence_auditor`** checks:

1. **Source authenticity** — the source exists and is retrievable.
2. **Citation entailment** — the quote actually says what the finding claims it
   says, in the direction claimed. A correlation reported in the source does
   not become an effect claim in the finding.
3. **Method quality** — the design can bear the weight of the claim being built
   on it.

Findings that fail go to `quarantined` in `evidence.json` **with the reason and
the original statement kept**. Quarantine is not deletion. The record shows
what was set aside and why, so a later reader can disagree with the call.

## Must never be written as an empirical result

- a hypothesis stated in a paper's introduction
- speculation from a discussion section
- a mechanism the authors assert but did not test
- "no effect" inferred from a non-significant result
- a causal claim inferred from a correlation
- a population-level conclusion inferred from a subgroup result

These are the six ways the run can be confidently wrong. Check for them
explicitly at the gate rather than trusting that nobody drifted.

## Paper count ≠ independent evidence count

Six papers on one dataset are one piece of evidence told six times. Group
findings by `dataset_key`, and report both numbers:

- **papers** — how many distinct sources are in the graph
- **independent evidence clusters** — how many distinct datasets those sources
  rest on

If the two numbers are equal, verify it before believing it: a shared dataset
usually shows up as an identical or near-identical dataset key, an overlapping
sample description, a preprint and its published version, or a reanalysis of an
earlier study. Two versions of the same paper are one cluster.

## What the script checks, and what it does not

`scripts/check_evidence.mjs` mechanically enforces the rules above that can be
decided by looking at the data — dangling edges, missing anchors, correlation
dressed as causation, refuted claims published as settled, quarantined items
stripped of their reason, cluster counts that contradict the sources.

It cannot check whether a locator is honest, whether a quote says what the
finding claims, or whether the reasoning is any good. Those are the
`evidence_auditor`'s job and the reader's, and a green run is not a claim that
the research is correct.
