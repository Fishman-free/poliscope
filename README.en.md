# Poliscope

> **Language / 阅读语言 / 語言：** [简体中文](README.md) · [繁體中文](README.zh-Hant.md) · [English](README.en.md)

> Seven AI scientists walk into a review. Nobody gets to paper over the disagreements.
>
> **Try it live:** [https://poliscope.tech/](https://poliscope.tech/) — the author's public deployment; open it and start, no install needed
>
> **Paper:** [*EpistemoBrain: A Seven-Scientist Council with Dual-Graph Evidence Governance for Auditable Scientific Blindspot Discovery*](docs/paper/epistemobrain_main.pdf) (ACL Findings 2026 format; LaTeX sources in `docs/paper/`)

Imagine you are reviewing a study claiming that "social media causes adolescent depression." The conclusion is polished, the citations are plentiful — but nobody tells you that this paper and five others all rely on the same dataset, and nobody tells you that the causal claim is really just a correlation. Poliscope was built for exactly this situation: a **deep-research agent** for contested questions in computational social science — **7 AI scientists with distinct specialties** gather evidence independently, cross-examine one another, and hunt for counterexamples, producing a **controversy evidence map where conclusions sit beside limitations, every claim traces to its source, and no dissent is deleted**.

## 1. Design Philosophy

### 1.1 The Problem

Having 7 agents take turns talking and an 8th agent write a summary produces something that looks lively but solves none of the real problems of scientific blind spots — disagreement gets flattened at the summary step. Poliscope starts from the opposite direction: first design the organizational structure and memory mechanisms a reliable controversy review needs, then place the 7 roles inside it. That structure is called **EpistemoBrain**.

### 1.2 All Seven Participate Throughout

The 7 seats (Theory Builder, Causal Scientist, Measurement & Construct Scientist, Statistics & Replication Scientist, Boundary & Context Scientist, Adversarial Falsifier, Evidence & Provenance Auditor) participate in **every formal research task**: each round has a speaking budget, seats `PASS` when they have nothing new, and all seven share one retrieval cache. Each seat has an independent role specification, private state, private memory, and different evidence-ordering weights; they gather evidence without seeing each other, commit to positions first, and only then exchange evidence — cross-examination is genuinely cross-examination. EpistemoBrain is the organizing brain without voting rights — not an 8th scientist.

### 1.3 Three Layers of Memory

**Private Brain** (each scientist's own exploration) → **Collective MemoBrain** (who proposed what, who attacked what, which disputes are unresolved, what to investigate next) → **Evidence Graph** (the auditable controversy map: only verified knowledge, no internal reasoning).

### 1.4 Dissent Is Preserved; the Council Does Not Vote

**Majority voting is forbidden as a way to decide scientific truth.** The seven-round protocol: independent precommitment → professional acquisition → evidence exchange → cross-examination → blindspot bounty → joint modeling → final rejudgment. Refuted positions stay traceable; genuine disagreements are written into named **Dissent Certificates** instead of being swallowed by the majority.

### 1.5 Dual-Graph Governance

Process and conclusion are stored apart: the seven's evidence-gathering, mistakes, and detours go into the **Process Graph**; formal conclusions must pass through the Scientific Event Ledger and the single writer — the **Graph Projector** — before landing on the **Evidence Graph**. This boundary is enforced at the **database privilege level**: the seven scientists have no permission to write the evidence graph at all.

### 1.6 The Evidence Gate

Every formal conclusion passes six checks: Schema → Semantic Deduplication → Source Verification → Citation Entailment → Method Quality → Graph Consistency. Evidence is graded A–D; an abstract never supports a high-confidence causal conclusion alone; hypotheses from an introduction or speculation from a discussion must not be written as empirical results; every `StudyFinding` is bound to its source, section, page, and exact quote.

### 1.7 Paper Count ≠ Independent Evidence Count

Six papers sharing one dataset are one piece of evidence told six times. The Evidence Lineage identifies shared datasets / overlapping samples / preprint vs. published versions, and the report shows both "number of papers" and "number of independent evidence clusters."

### 1.8 Epistemic Routing

Gaps exposed by the evidence graph become **blindspot bounties**, scored by impact, uncertainty, and investigability, then claimed by the seven — the evidence state drives the next investigation, not a moderator reading from a script. Blindspot is a first-class object, not a "limitations" paragraph.

## 2. Use Cases

- **Before writing a review**: draw the map first — which evidence supports, which opposes, where it is duplicated, where it is empty — and start reading with a list of gaps.
- **Evaluating a paper**: upload a PDF for paper review; the council points out weak arguments, insufficient evidence, and measurement/sample issues, with actionable suggestions.
- **Designing the next study**: unresolved blind spots are translated into a concrete, executable DiscriminatingStudy.
- **Review and defense**: every key judgment clicks back to the source text — you present a traceable, falsifiable research process, not a polished summary.
- **Sensitive topics**: for mental-health questions the report explicitly states "research assistance, not clinical diagnosis."

## 3. Product Strengths

- **Every key judgment clicks back to the source**: bound to source, section, page, and exact quote, admitted only after a three-layer audit.
- **Paper count ≠ independent evidence count**: the two numbers are shown separately.
- **Honest reporting of gaps**: missing model access, exhausted budgets, absent seats are listed one by one; the task is explicitly marked "completed with gaps" — a blank is never "no problem."
- **Evidence strength is graded**: Level A (full text) to Level D (news leads); low-level evidence never alone supports a high-confidence causal conclusion.
- **Dissent is permanently preserved**: no majority vote flattens it; a dissenting scientist receives a named `DissentCertificate`.
- **Blind spots drive the next investigation**: Blindspot is a first-class object, not a "limitations" paragraph.
- **The reasoning chain is visible, the chain-of-thought is not exposed**: raw model reasoning is collapsed under each round and labeled "process data, not evidence."
- **Correlation does not automatically upgrade to causation**: cross-sectional data + causal claim is intercepted by the evidence gate.
- **A downloadable final paper**: the seven seats' final judgments and references are synthesized into a structured paper, exportable as Markdown.
- **Four entrances, one core**: web / API / CLI / Agent Skill all go through the same research contract — no second path bypasses the evidence gate.

## 4. Capabilities

The center of the product is the **evidence map**, not a chatbox. Eight views:

| View | Question it answers |
|---|---|
| Research Brief | A 30-second grasp of the current judgment, evidence strength, counterexamples, and blind spots |
| Controversy Map | An interactive evidence map of claims, findings, conflicts, and blind spots |
| Audit Trail | The full event ledger: who made what judgment and when |
| Council | The seven scientists' full trajectory plus the conditional-consensus panel |
| Blindspot Radar | Blind spots ranked by impact and investigability |
| Evolution View | The timeline of claims and blind spots as evidence accumulates |
| Final Paper | A downloadable paper-form synthesis |
| Knowledge Base | Your long-term memory; retrieval hits participate directly in council reasoning |

## 5. How to Use

### 5.1 Web

Open [https://poliscope.tech/](https://poliscope.tech/), register, and start. Two features:

- **Deep research**: ask a contested question; the seven-scientist council investigates and cross-examines, producing an auditable evidence map;
- **Paper review**: upload the paper (PDF / Word / PPT / Excel / HTML / TXT, ≤ 20 MB); the council critiques argument rigor and evidence sufficiency, and you can keep asking in Follow-up afterward.

Deep research has one **bounded human checkpoint** before joint modeling: after the first 5 rounds the council pauses for your directional note (or an explicit pass); if you do nothing within the grace window (120 s by default), the server continues automatically with "no directional intervention" — the research never stalls because you walked away.

### 5.2 From a Coding Agent (Skill)

Call Poliscope directly from Claude Code / Codex, without switching to the web:

```bash
/plugin install Fishman-free/poliscope      # one command in Claude Code
npx github:Fishman-free/poliscope --help    # or zero-install via npx (Node 18+)
```

The Skill wraps the same `poliscope` CLI: describe the question → the Research Contract is generated for your confirmation (shown first, never submitted directly) → `poliscope start` → `confirm-claims` → `watch` / `status` → `export`. Against a deployed instance, `poliscope login --base-url <URL>` first. Full usage in [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md).

## 6. Be Honest with Its Conclusions

- **Not medical advice.** For mental-health questions the report explicitly states "research assistance, not clinical diagnosis."
- **No fabricated conclusions.** Missing model access, exhausted budgets, absent seats — each is marked explicitly; a blank is never "no problem."
- **Model confidence is not statistical evidence.** Model judgments, authors' own words, and statistical inference stay separately labeled.
- **Dissent is never deleted.** Refuted and quarantined positions keep their sources and remain traceable.

## 7. Docs & Deployment

- Self-hosting (Docker Compose) and the full CLI / API reference: [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md)
- Formal design specification: [`docs/superpowers/specs/2026-07-31-poliscope-design.md`](docs/superpowers/specs/2026-07-31-poliscope-design.md)
- Technical white paper (Chinese): [*Poliscope 技术白皮书*](docs/tech/tech.pdf)

## License

Poliscope's own code is open source under the [MIT License](LICENSE); the executive-memory foundation [MemoBrain](https://github.com/qhjqhj00/MemoBrain) keeps its own license — details in [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md#许可证与归属).
