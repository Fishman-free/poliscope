# What a run writes, and where

Everything goes under a directory the researcher picked, defaulting to
`docs/poliscope/<slug>/` in their own project. Write artifacts in the language
the researcher asked the question in; keep ids, filenames, and the JSON keys of
`evidence.json` in English regardless.

```
docs/poliscope/<slug>/
├── README.md                 index: the answer, the gaps, what is in here
├── contract.md               the Research Contract, as confirmed
├── brief.md                  the 30-second brief
├── evidence.json             the machine-checkable evidence graph
├── evidence.md               the evidence map, for reading
├── council.md                the seven rounds
├── blindspots.md             the ranked blindspot list
├── dissent.md                the dissent certificates
├── paper.md                  the synthesised final paper
└── scientists/<seat>.md      one file per seat: precommitment → final position
```

## `contract.md`

The Research Contract exactly as the researcher confirmed it. Nothing else.
If they changed something after seeing the draft, the file holds the changed
version — a contract that was never shown is not a contract.

## `brief.md` — 30 seconds

- the question, in one line
- what the evidence currently supports, and under what conditions
- what it does not settle
- the strongest counterexample found
- the top blindspots
- `has_gaps: true/false` and the limitations list

Conclusions and limitations sit **side by side**, never in separate sections
where one can be read without the other.

## `evidence.json` — the graph

One object. Ids are stable strings (`C1`, `S1`, `F1`, `B1`, `D1`).

```jsonc
{
  "question": "...",
  "claims": [
    { "claim_id": "C1", "statement": "...", "claim_type": "causal",
      "scope": "adolescents, US, 2010-2023" }
  ],
  "sources": [
    { "source_id": "S1", "title": "...", "year": 2023, "doi": "...",
      "level": "A" }
  ],
  "findings": [
    { "finding_id": "F1", "source_id": "S1", "locator": "Results 3.2, p. 7",
      "quote": "...", "design": "longitudinal", "dataset_key": "AddHealth" }
  ],
  "edges": [
    { "from": "F1", "to": "C1", "type": "SUPPORTS", "note": "..." }
  ],
  "blindspots": [
    { "blindspot_id": "B1", "statement": "...", "impact": 5,
      "uncertainty": 4, "investigability": 3, "exposed_by": ["C1"] }
  ],
  "dissent": [
    { "dissent_id": "D1", "seat": "adversarial_falsifier",
      "position": "...", "against": ["C1"],
      "resolving_evidence": "..." }
  ],
  "quarantined": [
    { "item": "F9", "reason": "quote is from the discussion section",
      "kept_statement": "..." }
  ],
  "consensus": [
    { "claim_id": "C1", "status": "supported",
      "conditions": ["..."] }
  ],
  "counts": { "papers": 12, "independent_clusters": 7 },
  "gaps": ["seat replication_scientist did not return in round 6"]
}
```

`claim_type` is one of `causal`, `associational`, `measurement`, `boundary`,
`mechanism`. `status` is one of `supported`, `contested`, `insufficient`.

An edge whose target claim still comes out `supported` while the edge is a
`REFUTES` or `CONFOUNDS` must carry a non-empty `resolution` saying why it did
not change the verdict. There is no silent overruling.

`gaps` may not be empty when `blindspots` is empty — "we found no blindspots"
is only reportable alongside the reason to believe that.

## `evidence.md` — the map, for a human

Claims as sections. Under each: the supporting findings with their locators,
the refuting ones, the conditions, and the blindspots it exposes. Show both
counts — papers and independent clusters — at the top. A reader must be able to
go from any sentence here to a locator in a real source.

## `council.md` — the seven rounds

One section per round. Round 1 shows all seven precommitments **as sealed**.
Round 4 shows every challenge with its response. Round 7 shows each seat's
round 1 → round 7 movement and what moved it. The researcher's directional
note, if given, is quoted in the round 6 section and labelled as not a
scientific judgment.

Raw reasoning is not reproduced. Structured actions, evidence used, challenges
and responses, conclusions and confidence changes, and auditable decision
summaries are what belongs here.

## `blindspots.md`

The ranked list with each gap's three scores and who raised it, separated into
what could be investigated now and what is out of reach in this run. Where a
gap implies a study that would settle it, state that study concretely: what
would be measured, in whom, and what result would count as which answer.

## `dissent.md`

One certificate per surviving disagreement:

- the seat holding it
- the position it holds, in its own words
- what it is against — claim ids
- why it was not resolved: the challenge that went unanswered, or the evidence
  that does not exist yet
- the evidence that would resolve it

A run with no dissent certificates is possible but unusual. Before writing an
empty file, check that no `CHALLENGE` went unanswered and no `REFUTES` edge is
still live — an empty dissent file is a claim in itself.

## `paper.md`

Abstract, the question and why it is contested, method, findings organised by
claim with conditions, limitations, open questions, references. Every claim in
it traces to a claim id in `evidence.json`. The paper **assembles** — it
produces no judgment that is not already in the graph, the consensus, or a
dissent certificate.

## `scientists/<seat>.md`

For each of the seven: its role instruction, its sealed round-1 precommitment,
the challenges it raised and received, its blindspot claims, its final
position, and — where the position moved — what moved it.
