# Poliscope

> **Language / 阅读语言 / 語言：** [简体中文](README.md) · [繁體中文](README.zh-Hant.md) · [English](README.en.md)

> Seven AI scientists walk into a review. Nobody gets to paper over the disagreements.
>
> **Try it live:** [https://poliscope.tech/](https://poliscope.tech/) — the author's public deployment; open it and start, no install needed
>
> **Paper:** [*EpistemoBrain: A Seven-Scientist Council with Dual-Graph Evidence Governance for Auditable Scientific Blindspot Discovery*](docs/paper/epistemobrain.pdf) (ACL Findings 2026 format; [PDF](docs/paper/epistemobrain.pdf)) · [技术白皮书](docs/tech/tech.pdf)

Imagine you are reviewing a study claiming that "social media causes adolescent depression." The conclusion is polished, the citations are plentiful — but nobody tells you that this paper and five others all rely on the same dataset; nobody tells you that the causal claim is really just a correlation; nobody tells you that the authors themselves admit they could not rule out reverse causation.

Poliscope was built for exactly this situation. It is a **deep research agent** for contested questions in computational social science: you submit a dubious question, and it organizes **7 AI scientists with distinct specialties** to gather evidence independently, cross-examine one another, hunt for counterexamples and flaws, and finally produce a **controversy evidence map where conclusions and limitations sit side by side, every claim is traceable to its source, and no dissent is deleted**.

Not a fluent summary you cannot verify.

---

## 1. Design Philosophy: Why Poliscope Looks the Way It Does

### 1.1 This Is Not "7 Chatbots + 1 Summarizer"

This is the most common misunderstanding, so it deserves to be cleared up in the first minute: having 7 agents take turns talking and an 8th agent write a summary produces something that looks lively but **solves none of the real problems of scientific blind spots** — disagreement gets flattened in the summary step, and nobody's judgment is ever genuinely challenged by somebody else's evidence.

Poliscope's design started from the opposite direction: first decide what organizational structure and memory mechanisms a reliable controversy review needs, then place the 7 roles inside that structure. The structure is called **EpistemoBrain**. Below, we unfold it layer by layer, explaining the "why" of each decision.

### 1.2 All Seven Scientists Participate Throughout; Costs Are Controlled Elsewhere

Any judgment that "this role won't be needed this time" is a bet that the blind spot will not appear in the skipped dimension. A measurement scientist looks irrelevant to a causal dispute — but self-report bias is often the true root of that very dispute. So **all 7 seats participate in every formal research task**: each round has a speaking budget, seats `PASS` when they have nothing new, re-reasoning is triggered only after semantic deduplication, and all seven share one retrieval cache. It is harder to engineer, but it buys the guarantee that "no blind spot is missed because a scheduling decision got it wrong."

The seven are: Theory Builder, Causal Scientist, Measurement & Construct Scientist, Statistics & Replication Scientist, Boundary & Context Scientist, Adversarial Falsifier, and Evidence & Provenance Auditor. EpistemoBrain is the organizing brain without voting rights — not an 8th scientist, and it does not represent any academic position.

Each seat has **an independent role specification, independent private state, independent private memory, and different evidence-ordering weights**. They gather evidence without seeing each other, commit to positions first, and only then exchange evidence — "cross-examination" is genuinely cross-examination, not restatement of the same line of thought.

### 1.3 Dissent Is Preserved; the Council Does Not Vote

**The system forbids majority voting as a way to decide scientific truth.** Whether a claim is true does not depend on how many of the seven raise their hands. The council follows a seven-round protocol:

1. **Independent Precommitment** — the seven commit initial judgments and confidence levels without seeing each other's answers (sealed first, read later, ruling out herding and post-hoc consistency);
2. **Professional Acquisition** — each gathers evidence in parallel according to their own evidence needs;
3. **Evidence Exchange** — structured evidence is exchanged, not private reasoning histories;
4. **Cross-Examination** — targeted attacks on causality, measurement, statistics, boundaries, sources, and hidden assumptions;
5. **Blindspot Bounty** — gaps exposed by the evidence map are scored, ranked, and claimed;
6. **Joint Modeling** — produces a conditional consensus: shared understanding, strongest supporting and opposing positions, hinge variables, scope boundaries, unresolved conflicts, and falsification conditions;
7. **Final Rejudgment** — the seven judge independently again, recording how beliefs changed under evidence, and who still dissents.

Refuted and quarantined positions do not disappear; they remain traceable. The final output is expressed as a **conditional consensus**, and genuine disagreements are written into named **Dissent Certificates** rather than quietly swallowed by the majority. On the web workspace you can see every challenge, every change of position, and what each scientist ultimately held.

### 1.4 Dual-Graph Governance — Process and Conclusion Are Stored Apart

Everything the seven do while gathering evidence, cross-examining, failing, and wandering (the **Process Graph**, managed by executive memory) is recorded — but "process" must not automatically become "fact," or a scientist's momentary bad guess could quietly upgrade into an official conclusion.

Therefore every formal change must first be written into the **Scientific Event Ledger**, and only the single writer — the **Graph Projector** — reviews it and lands it on the **Evidence Graph** (the controversy map: atomic claims, study findings, sources, constructs, blind spots, debate capsules...). This boundary is enforced at the **database privilege level**, not by coding discipline — the seven scientists have no permission to write the evidence graph at the database level, so even a bug cannot touch formal evidence.

Why two graphs instead of one? Because "how did this scientist complete the research" and "what do we now know about this research question" are two different questions. If tool-call steps and academic evidence share one graph, you cannot tell process from evidence; a single compression step could also fold away unresolved disputes. With two graphs, process may be folded; evidence never disappears.

### 1.5 The Evidence Gate — Every Formal Conclusion Passes Six Checks

Candidate events pass through: **Schema Validation → Semantic Deduplication → Source Verification → Citation Entailment → Method Quality → Graph Consistency**. Concretely:

- **Evidence levels**: only full text with exact quotes counts as **Level A**; evidence with only abstracts, second-hand descriptions, or news leads is honestly downgraded — abstracts are never allowed to support high-confidence causal conclusions on their own;
- **Citation entailment**: hypotheses from a paper's introduction, speculation from its discussion, mechanisms the authors never tested — none may be written as empirical results; cross-sectional data must not be dressed up as causal evidence (the `CausalUpgradePolicy` intercepts exactly this combination);
- **Three-layer audit**: source authenticity, citation entailment, and method quality are verified one by one;
- **Precise provenance**: every formal `StudyFinding` is bound to at least a source, study, section, page (or equivalent location), exact quote, extracting agent, and verification status — every key judgment clicks back to the original text.

### 1.6 Paper Count ≠ Independent Evidence Count

If six papers share the same dataset, the same research team, or one is an extension of another, they are one piece of evidence told six times. The **Evidence Lineage** automatically identifies dependencies — **same dataset / overlapping samples / preprint vs. published version / extension / review containing the original studies** — and the interface shows both "number of papers" and "number of independent evidence clusters." Only when these two numbers are separated do you see whether a "consensus" is seven independent observations or one observation retold seven times.

Four further defenses guard against "seven scientists sharing the same evidence error": blind evidence review (hiding author and journal prestige signals), independent dual extraction (high-impact findings extracted twice through separate paths), source diversity constraints (automatically flagging a blind spot when all supporting evidence is single-sourced), and adversarial retrieval (actively searching for refutations, null results, and replication failures rather than only papers that support the working position).

### 1.7 Long-Term Memory: MemoBrain Foundation + Your Knowledge Base

The executive memory foundation of the seven-scientist council is [MemoBrain](https://github.com/qhjqhj00/MemoBrain) (ACL Findings 2026): each scientist has independent private memory and recalls evidence from their own role-specific perspective; refuted hypotheses enter quarantine and can be reactivated when resurrection conditions are met; when history is compressed, both sides of an argument are preserved in debate capsules. MemoBrain's three native operations are redefined in scientific semantics:

- **Flush → Quarantine (isolate, not delete)** — a refuted claim does not disappear; it changes status, and the record says who refuted it, what evidence is missing, and what conditions would resurrect it;
- **Fold → Dialectical Fold (compress dialectically, not shrink text)** — a dispute may only be folded into a DebateCapsule after retaining shared understanding, the strongest supporting evidence, the strongest opposing evidence, hinge variables, scope boundaries, unresolved conflicts, and falsification conditions; the original nodes remain and the capsule links back to them;
- **Recall → Perspective Recall (role-specific recall)** — each scientist receives their own private process memory plus a slice of the same evidence graph projected for their own concerns: the causal scientist sees confounding and reverse causation; the measurement scientist sees construct conflicts.

**Your** memory lives in the knowledge base. Uploaded PDFs, Word, PPT, Excel, CSV files, even pasted text, are parsed, stored, and reused across tasks — this is the researcher's long-term memory: organize once, benefit forever. When a task links a knowledge base, the council's acquisition rounds **search your knowledge base** and bring hits into the scientists' judgment context (explicitly labeled "non-formal evidence") — your literature genuinely participates in the seven's reasoning instead of lying in a folder nobody reads.

### 1.8 Epistemic Routing — Let the Evidence Map Decide What to Investigate Next

The council is not a mechanical run through seven rounds. Gaps exposed by the evidence graph generate **blindspot bounties**, scored and ranked by impact, uncertainty, investigability, novelty, and cost, then broadcast for the seven to claim — **the evidence state drives the direction of the next investigation, rather than a moderator reading from a script**. This is why Blindspot is a first-class object in the product, not a "limitations" paragraph at the end of the report.

---

## 2. Product Strengths

- **Every key judgment clicks back to the source text.** Every formal `StudyFinding` is bound to at least a source, study, section, page (or equivalent location), exact quote, extracting agent, and verification status, and must pass the three-layer audit of source authenticity, citation entailment, and method quality before entering the graph.
- **Paper count ≠ independent evidence count.** If six papers share one dataset or one lab's sample, they count as one independent piece of evidence — the report shows both numbers separately.
- **Honest reporting of gaps instead of faked completeness.** Missing model providers, exhausted budgets, absent seats — all are listed one by one in the report, and the task status is explicitly marked "completed with gaps." A blank is never silently treated as "no problem."
- **Evidence strength is graded.** Levels A (full text with exact quotes) through D (web/news leads) are all marked; B/C/D evidence never alone supports a high-confidence causal conclusion.
- **Dissent is permanently preserved; no majority vote flattens it.** Joint modeling is forbidden from deciding scientific truth by voting; refuted, quarantined, and folded positions remain traceable, and a dissenting scientist receives a named `DissentCertificate`.
- **Blind spots drive the next investigation.** Gaps in the evidence graph generate "blindspot bounties," ranked by impact, uncertainty, and investigability, then claimed by the seven — Blindspot is a first-class citizen of the product, not a "limitations" paragraph.
- **The reasoning chain is visible, the chain-of-thought is not exposed.** The Council view shows each scientist's full trajectory — precommitment → rounds of actions → final rejudgment — with raw model reasoning collapsible under each round. The system clearly labels it: process data, not evidence.
- **Correlation does not automatically upgrade to causation.** Causal statements must pass causal review with explicit limitations; the combination of cross-sectional design + causal claim is intercepted and logged by the evidence gate.
- **A downloadable final paper when the run completes.** At terminal state, a synthesis integrates the seven seats' final judgments, the conditional consensus, and the full reference list into a structured paper (abstract / body / references / limitations / investigation process), exportable as Markdown.
- **Every gap is named, not just counted.** Which seat was absent, which round did not run or failed — all written out with the scientist's and phase's names, pinned in the task header.
- **Four entrances, one core.** Web workspace, HTTP/SSE API, CLI, and Claude Code / Codex Agent Skill all go through the same research contract — there is no second path that bypasses the evidence gate.
- **Queue visibility, session control.** When queued, the interface tells you exactly how many tasks are ahead and which one the worker is running; the session history supports per-item deletion and clear-all — your data, your call.

---

## 3. Capability Overview

The workspace provides eight views. The center of the product is the **evidence map**, not a chatbox:

| View | Question it answers |
|---|---|
| **Research Brief** | A 30-second grasp: current judgment, strength of evidence, counterexamples, blind spots, and the next study that should be done |
| **Controversy Map** | An interactive evidence map of claims, findings, constructs, boundaries, conflicts, and blind spots — every node has a human-readable detail panel plus the auditable raw record |
| **Audit Trail** | The full event ledger: who made what judgment, when, and using which evidence |
| **Council** | The seven scientists side by side: precommitment confidence → rounds of actions → final rejudgment, plus the conditional consensus panel |
| **Blindspot Radar** | A radar chart of blind spots ranked by impact and investigability; click one to read "what this blind spot is and what happens if you trust the conclusion anyway" |
| **Evolution View** | The timeline of claims and blind spots as evidence accumulates |
| **Final Paper** | A paper-form synthesis with conclusions, references, limitations, and investigation process, downloadable |
| **Knowledge Base** | Your long-term memory: upload literature, paste notes; retrieval hits participate directly in council reasoning |

---

## 4. How to Use

### 4.1 Web: Two Features Are Live

Open the **public demo instance `https://poliscope.tech/`**, click "进入工作台" (or visit `/workspace` directly) and register an account. Two features are live:

- **Deep research**: ask a contested question and the seven-scientist council investigates independently and cross-examines, producing an auditable evidence map;
- **Paper review**: first **upload the paper to review** (PDF / Word / PPT / Excel / HTML / TXT, ≤ 20 MB each), the council reads its research question, main claims and evidence, then critiques argument rigor, evidence sufficiency, and measurement / sample representativeness with improvement suggestions. After the review you can keep asking in "补充提问" (Follow-up), and the model answers grounded in the paper's full text.

During a run the page auto-follows council progress; when it finishes, view conclusions on the page or export Markdown / JSON reports.

### 4.2 Calling from a Coding Agent (Skill)

If you work in Claude Code, Codex, or another coding agent that honors the `AGENTS.md` convention and prefer not to switch to the web UI, you can have the agent call Poliscope directly. The repo ships four identical copies of the Skill:

```
skills/poliscope/             Skill inside the Claude Code plugin (available after /plugin install)
.claude/skills/poliscope/     Claude Code Agent Skill
.codex/skills/poliscope/      Codex copy
.agents/skills/poliscope/     Generic AGENTS.md-convention copy
```

The Skill is a thin wrapper around the same `poliscope` command-line tool (Python 3.12). Three installation options:

**Option 1: `/plugin install` in Claude Code (recommended — one command installs plugin + Skill):**

```bash
/plugin install Fishman-free/poliscope
```

After install, type `/poliscope` directly to invoke it (or `/plugin marketplace add Fishman-free/poliscope` to add it to the marketplace list first).

**Option 2: `npx` zero-install (Node 18+ only; underneath it pulls the Python CLI via the repo venv or uvx):**

```bash
npx github:Fishman-free/poliscope --help
```

Once published to the npm registry, `npx poliscope --help` works the same way. The Python-ecosystem equivalent also works:

```bash
uvx --from "git+https://github.com/Fishman-free/poliscope.git" poliscope --help
```

**Option 3: Clone the repo and install:**

```bash
git clone https://github.com/Fishman-free/poliscope.git
cd poliscope
uv sync                       # or pip install -e ".[dev]"
poliscope --help              # prints help = installed
```

The Skill does not hardcode any server address; its CLI defaults to `http://localhost:8000` (a locally running API). To reach a deployed instance, pass `--base-url` on every call and **log in once**:

```bash
poliscope login --base-url <URL>     # enter username/password; use register on first use
```

The token is saved in `~/.poliscope/credentials.json` (one token per base URL, expires in 30 days) and every subsequent command attaches it automatically; non-interactive environments (agent scripts) can set `POLISCOPE_API_TOKEN` instead, which takes precedence over the credentials file. A local API needs no login — `poliscope health` confirms the API is alive first.

Workflow: describe the research question in natural language → the Skill generates a Research Contract for your confirmation (**shown to you first, never submitted directly**) → on confirmation, call `poliscope start` (suggests claims) → `poliscope confirm-claims` → `poliscope watch` / `status` → `poliscope export`. For a deployed instance, `poliscope login` once first (above). The task returns a `task_id` you can resume after switching machines or closing conversations.

The full CLI reference (including `pause`/`resume`, `council-preview`/`council-guidance`, `export-docs`) lives in [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md).

### 4.7 Access Troubleshooting: If You Cannot Open the Site

**Symptom: the browser reports "This site can't be reached", "the connection was closed", or "the connection is not secure".**

Modern browsers (especially Edge / Chrome) automatically upgrade `http://` to `https://`. If the site only serves `http://` (no HTTPS), that upgrade fails — the page appears unreachable, or you can see the homepage from GitHub but the data does not load.

**All you need to do (pick one):**

- Change `https://` back to `http://` in the address bar and press Enter;
- Open the site in an **InPrivate / incognito window**;
- Turn off "Automatically use HTTPS" in Edge (Settings → Privacy, search, and services → turn off "Automatically use HTTPS").

**Symptom: another URL (self-hosted / LAN) cannot be opened.**

Ask whoever runs the site for the **correct address and port** (commonly `http://localhost:8080/`; for a public deployment `http://<IP>/` or `http://<domain>/`), then visit that directly.

---

## 5. Be Honest with Its Conclusions

- **It is not medical advice.** For mental-health-related questions, the report explicitly states "this is research assistance, not clinical diagnosis."
- **It will not fabricate conclusions to look conclusive.** If the deployment has no working model access, the system honestly reports "no model connected" and some scientists' judgments are explicitly marked absent — a blank is never treated as "no problem." That is a design principle, not a bug.
- **Model confidence is not statistical evidence.** The report keeps model judgments, authors' own words, and statistical inference separately labeled, and never substitutes model confidence for statistical uncertainty.
- **Refuted and quarantined positions do not disappear.** Disagreements and counterexamples in the report keep their original sources, so you can judge for yourself which side to trust.

---

## 6. Want to Deploy It Yourself, or Understand How It Works?

- Self-hosting (Docker Compose one-command startup, domain setup), full CLI/API reference, system architecture and the seven-scientist council protocol in detail, capability overview and design philosophy — all in [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md).
- Formal design specification: [`docs/superpowers/specs/2026-07-31-poliscope-design.md`](docs/superpowers/specs/2026-07-31-poliscope-design.md).
- System paper: [*EpistemoBrain: A Seven-Scientist Council with Dual-Graph Evidence Governance for Auditable Scientific Blindspot Discovery*](docs/paper/epistemobrain.pdf) (ACL Findings 2026 format; sources in `docs/paper/`)
- Technical white paper (Chinese): [*Poliscope 技术白皮书*](docs/tech/tech.pdf) (LaTeX sources + compiled PDF in `docs/tech/`; architecture, council, memory, dual-graph, evidence gate, token optimization, reliability, evaluation)

---

## License

Poliscope's own code is open source under the [MIT License](LICENSE). It uses [MemoBrain](https://github.com/qhjqhj00/MemoBrain) as the methodological foundation for executive memory; MemoBrain retains its own license — attribution details in [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md#许可证与归属).
