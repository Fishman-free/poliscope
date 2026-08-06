# Poliscope

> **中文 / Language / 日本語** [简体中文](README.md) · [繁體中文](README.zh-Hant.md) · [English](README.en.md)

> Seven AI scientists, one scientific review with no fence-sitting. Welcome to the scene.

Picture this: you're reviewing a study on "social media causing depression in adolescents." The conclusion looks polished and the citations look complete — but no one tells you that this paper and five others draw on the same dataset; no one tells you that the key causal claim is actually just a correlation; and no one tells you that the authors themselves admit they never ruled out reverse causation.

Poliscope was built for exactly this scenario. It is a **deep research agent** for contested questions in computational social science: feed it a questionable question, and it organizes **7 AI scientists, each with their own specialty**, to gather evidence independently, cross-examine one another, and deliberately hunt for counterexamples and holes, ultimately producing a **contested evidence map where conclusions and limitations sit side by side, evidence is traceable, and dissent is never deleted**.

Not a summary that reads smoothly but cannot be verified.

---

## What ships today is not "7 chatbots + 1 summarizer bot"

This is the most misunderstood thing about Poliscope, so it's worth making clear in the first minute: have 7 agents each say their piece in turn, then have an 8th agent write the summary — the result looks lively, but it **solves none of the real research blindspot problems** — disagreements get flattened in the summarization step, and nobody's judgment has truly been challenged by someone else's evidence.

Poliscope's design went the opposite way from the start: first decide "what organizational structure and memory mechanisms a reliable contested review should have," then fit the 7 roles into that structure. That structure is called **EpistemoBrain**, built on four pillars:

### Pillar 1: All seven participate throughout; cost is controlled by other means

Every judgment of "this role isn't needed this time" is a bet that the blindspot won't show up in the dimension being skipped. A measurement expert can look irrelevant to a causal controversy — but self-report bias is often the true root of that causal controversy. So **all 7 seats participate throughout every formal research task** — each round of speech has a budget, `PASS` when there is no new information, re-reasoning is triggered only after semantic deduplication, and the seven share a single retrieval cache. It's more work on the engineering side, but in exchange you get: "a blindspot that should have been found won't be missed because of one scheduling misjudgment."

The seven are: theory builder, causal inference expert, measurement and construct expert, statistics and replication expert, boundary and context expert, adversarial falsifier, and evidence and provenance auditor. EpistemoBrain is the non-voting organizational brain — not an 8th scientist, and it represents no academic position.

### Pillar 2: Dissent fidelity — the council is not a vote

Each seat has an independent role specification, independent private state, an independent Private MemoBrain, and different evidence-ranking weights; they are invisible to one another during the evidence-gathering phase — each forms a judgment first, then evidence is exchanged. "Cross-examination" is truly cross-examination, not a restatement of the same line of thinking.

**The system forbids majority voting from directly adjudicating scientific truth.** Refuted and quarantined positions do not disappear; they remain traceable. The final result is expressed as conditional consensus, and genuine disagreements are written into dissent certificates (`DissentCertificate`) instead of being quietly swallowed by the majority view. You can see on the web page every cross-examination, every shift in position, and what each scientist ultimately held to.

### Pillar 3: Dual-graph governance — process and conclusions must be stored separately

The full journey of the seven — gathering evidence, cross-examining, making mistakes, going down wrong paths — is recorded (Process Graph, managed by MemoBrain), but this "process" cannot automatically become "fact"; otherwise, a scientist's momentary wrong guess could quietly be promoted into a formal conclusion in the report.

So every formal change must first be written to the **Scientific Event Ledger**, and then, after review by the sole writer, the **Graph Projector**, land in the **Evidence Graph**. This step is enforced at the database permission level, not by code discipline — at the database level, the seven scientists simply have no permission to write to the evidence graph.

### Pillar 4: Long-term memory and intelligent retrieval

The execution-memory foundation of the seven-scientist council is [MemoBrain](https://github.com/qhjqhj00/MemoBrain): each scientist has independent private memory and recalls evidence from the vantage point of their own role; refuted hypotheses enter quarantine and can be revived when conditions are met; when history is compressed, evidence from both sides is preserved in debate capsules (`DebateCapsule`).

And **your memory** lives in the knowledge base. Uploaded PDFs, Word, PPT, Excel, CSV — even text pasted in directly — are parsed, saved, and reused across tasks. This is the researcher's long-term memory: organize once, effective for the long run. Once a task is linked to a knowledge base, the council **retrieves your knowledge base** when gathering evidence and brings the matching excerpts into the scientists' judgment context (explicitly labeled "informal evidence") — your literature genuinely participates in the seven's reasoning instead of lying in a folder nobody reads.

---

## Product highlights

- **Every key judgment can be clicked back to the original text.** Every formal `StudyFinding` binds at minimum the source, the study, the section, the page (or an equivalent locator), the exact quote, the extracting agent, and the verification status; it must pass three layers of review — source authenticity, citation entailment, and methodological quality — before entering the graph.
- **Paper count ≠ independent evidence count.** If six papers share the same dataset or samples from the same lab, they count as only one piece of independent evidence — the report displays these two numbers separately.
- **Reports gaps honestly instead of faking complete results.** A model vendor not configured, budget exhausted, a seat absent — each is listed item by item in the report, and the task status is explicitly marked "completed with gaps."
- **Evidence strength grading.** All four levels are marked, from Level A (full text and exact original quotes available) to Level D (web/news leads); Levels B/C/D are never used alone to support high-confidence causal conclusions.
- **The reasoning chain is visible.** The council page shows each scientist's complete chain — precommitment → per-round actions → final re-adjudication, with raw model reasoning collapsed under the corresponding round and expandable. The system explicitly notes: that is process data, not evidence.

---

## How to use

### The URL: read this first

Poliscope has no officially hosted public website — it is open source under the MIT license, and **the URL you visit depends on the deployment**:

- **One-command Docker Compose deployment (recommended)**: the default local address is **`http://localhost:8080/`** (`POLISCOPE_SITE_HOST_PORT` in `.env` determines the actual port).
- After deploying to a server, change `POLISCOPE_SITE_ADDRESS` in `.env` to your domain; Caddy automatically issues HTTPS, and the URL becomes your domain.
- **Can't open `http://localhost:8080/`?** Check two things first: `grep POLISCOPE_SITE_HOST_PORT .env` (to see the actual port) and `docker compose ps caddy` (to see the Caddy container binding). If port 80 is taken by another project, the real address is `http://localhost:<PORT>/`. After changing `deploy/caddy/Caddyfile`, you must rebuild with **`docker compose up -d --build caddy`** — the Caddyfile is baked into the image, and it doesn't take effect until you rebuild.

The root path `/` is the public landing page; the research evidence workspace is at `/workspace` — **your first visit shows a register/login page**: register an account to enter (the machine remembers your login state — no login needed within 30 days; log in again after switching machines/expiry). The account system has replaced the old shared password (`POLISCOPE_SITE_USERNAME` / `POLISCOPE_SITE_PASSWORD` in `.env` are no longer used and can be deleted).

### Start research in three steps

1. **Enter the workspace.** Open `http://localhost:8080/` and click "Enter workspace" (or visit `/workspace` directly). Register an account on first use (username + password); after that, this machine logs you in automatically.
2. **Create a task.** Write out your contested question clearly. Optionally:
   - Link a **knowledge base** (your literature participates in the council as Level A user-provided sources; click "Manage knowledge base" when you need to organize documents);
   - Upload a **PDF** (task-level; after full-text verification, it enters the evidence graph as Level A);
   - No need to fill in the model every time — the **model settings** panel in the right sidebar saves it once, and all subsequent tasks use it automatically; without a setting, the system default model is used.
   
   The system first lists the atomic claims that can be investigated separately; check the ones you care about, then click "Confirm and start research."
3. **Track and export.** During the research, the page automatically follows the council's progress: each round automatically switches to the corresponding view, and after the blindspot bounty round ends you have one more **direction-adjustment opportunity** (submit a short directional note, or explicitly leave it blank and continue). When it finishes, you can view the conclusions on the web page, or export Markdown / JSON reports for archiving.

The whole process usually takes a few dozen minutes — a seven-scientist council with item-by-item evidence verification is inherently slower than a one-sentence summary; this is a trade-off made for reliability.

### Knowledge base: your long-term memory

The "Knowledge base" tab in the workspace (in the top tab bar when you have a task; reached via the "Knowledge base" segment button at the top of the home page when you don't):

- Create a knowledge base → upload **PDF / TXT / MD / CSV / DOCX / PPTX / XLSX** (single file ≤ 20 MB) → the full text is parsed and saved automatically; legacy .doc / .ppt / .xls can't be parsed — the system clearly prompts you to save them in a new format instead of pretending they parsed successfully; knowledge bases are isolated per account;
- Don't want to organize things into files? **Paste text** directly (title + content) and it goes into the knowledge base too — notes, web excerpts, and report fragments all work, with retrieval and Level A treatment exactly the same as uploads;
- Link a knowledge base when creating the task; the council's retrieval round verifies your documents as Level A sources and brings the retrieval hits into the scientists' judgment context;
- Keyword search is Postgres full-text search + substring matching: English gets stem matching, **Chinese is substring matching** (retrieval locates and expands the source text within a paragraph, with no tokenization — a known boundary of the current version);
- Deletion protection: knowledge bases/documents that are already referenced by tasks or have produced evidence refuse deletion, so evidence lineage can never be erased.

### Right sidebar: model settings, Skills, and session history

The workspace's right sidebar is visible on every page and handles three things (all isolated per account):

- **Model settings**: fill in Base URL / API Key / model name once and save (stored on the server, auto-applied when creating a task); all subsequent tasks use it. The API Key is stored only on the server and is **never echoed back on any page** — the UI only shows a "configured" status; when changing the config, leaving it blank keeps the old key. Without a setting, the system default model configured by the deployment is used.
- **Skills**: paste a GitHub skill repository URL (e.g., `https://github.com/owner/skill-name`) into the input box at the bottom of the panel and click "Download and add" to fetch its SKILL.md and add it to the list; check = enable. Once enabled, newly created tasks carry it by default, and the worker injects the skill content into the council prompt as **"researcher-provided skill instructions (informal evidence)"** — it guides the scientists' investigation methods but is never used as evidence to support or refute claims.
- **Session history**: all research sessions of this account are listed in reverse chronological order; click to jump — no more copy-pasting task IDs; after switching machines or closing the conversation, you can come straight back from the history.

### Using Skills: call directly from a coding agent

If you usually work in Claude Code, Codex, or another coding agent that supports the `AGENTS.md` convention and don't want to switch to the web page, you can have the agent call Poliscope directly. The repo contains four copies of the Skill with identical content, for different agent environments to read (`.claude-plugin/plugin.json` is the Claude Code plugin metadata and doesn't contain SKILL.md itself):

```
skills/poliscope/             Skill inside the Claude Code plugin (available after /plugin install)
.claude/skills/poliscope/     Agent Skill for Claude Code
.codex/skills/poliscope/      Corresponding copy for Codex
.agents/skills/poliscope/     Copy read by the generic AGENTS.md convention
```

Under the hood, all Skills call the same `poliscope` command-line tool (Python 3.12). Pick one of three installation methods:

**Method 1: `/plugin install` in Claude Code (recommended — one command installs both the plugin and the Skill):**

```bash
/plugin install Fishman-free/poliscope
```

After installation, type `/poliscope` to invoke it directly (you can also `/plugin marketplace add Fishman-free/poliscope` to add it to the marketplace list and browse).

**Method 2: zero-install run via `npx` (Node 18+ is enough; under the hood it automatically pulls the Python CLI via the repo's venv or uvx):**

```bash
npx github:Fishman-free/poliscope --help
```

Once published to the npm registry, `npx poliscope --help` works the same way. You can also run the Python-ecosystem equivalent directly:

```bash
uvx --from "git+https://github.com/Fishman-free/poliscope.git" poliscope --help
```

**Method 3: clone the repo for a permanent install:**

```bash
git clone https://github.com/Fishman-free/poliscope.git
cd poliscope
uv sync                       # or pip install -e ".[dev]"
poliscope --help              # if it prints help, it's installed
```

The Skill itself doesn't bundle any server address; the command line it calls connects to `http://localhost:8000` (the API started locally) by default. When connecting to a deployed instance, pass `--base-url` on every call, and **log in once first**:

```bash
poliscope login --base-url <URL>     # enter username/password; use register on first use
```

The token is stored in `~/.poliscope/credentials.json` (one token per base URL, expires after 30 days), and all subsequent commands carry it automatically; in non-interactive environments (agent scripts), you can also set the `POLISCOPE_API_TOKEN` environment variable directly, which takes precedence over the credentials file. The local API needs no login — `poliscope health` can confirm the API is alive first.

Workflow: you describe the research question in natural language → the Skill generates a Research Contract for confirmation (**shows it to you first; it is never submitted directly**) → after you confirm, it calls `poliscope start` in turn (giving suggested claims) → `poliscope confirm-claims` → `poliscope watch` / `status` → `poliscope export`. When connecting to a deployed instance, run `poliscope login` once first (see above). The task returns a `task_id`, so you can keep tracking it after switching machines or closing the conversation.

> **Known honest gaps**: the task-level PDF upload endpoint (`POST /api/tasks/{task_id}/papers/upload`) itself works, but the Skill and the command line don't yet have corresponding wrapper commands — to attach a PDF, call the upload endpoint directly with curl, or do it in the web workspace.

---

## Be honest about its conclusions

- **It is not medical advice.** For mental-health-related questions, the report explicitly notes "this is research assistance, not a clinical diagnosis."
- **The login token has one known URL-leak surface.** Browser event streams (EventSource) can't carry request headers, so the login token of the real-time progress stream ends up in URL query parameters, and may therefore be recorded in nginx access logs. The impact is limited for local or trusted-network deployments; if you care, turn off or shorten the access-log retention (this is a known boundary of the current version).
- **It won't fabricate conclusions to look "conclusive."** If the deployment hasn't configured AI model access properly, the system honestly reports "no model connected," and some scientists' judgments are explicitly marked as absent.
- **Model confidence is not statistical evidence.** The report labels model judgments, the authors' own words, and statistical inference separately, and never substitutes model confidence for statistical uncertainty.
- **Refuted and quarantined positions don't disappear.** The disagreements and counterexamples in the report keep their original sources, so you can judge for yourself which side to believe.

---

## Want to deploy it yourself, or figure out how it does all this?

- Self-hosted deployment (one-command Docker Compose startup, password config, attaching a domain), full command-line/API usage, system design and the details of the seven-scientist council protocol, and what isn't done yet — all written in [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md).
- Formal design specification: [`docs/superpowers/specs/2026-07-31-poliscope-design.md`](docs/superpowers/specs/2026-07-31-poliscope-design.md).

---

## License

Poliscope's own code is open source under the [MIT license](LICENSE). It uses [MemoBrain](https://github.com/qhjqhj00/MemoBrain) as the methodological foundation of its execution memory; MemoBrain retains its own license — for attribution details, see [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md#许可证与归属).
