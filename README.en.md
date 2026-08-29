# Poliscope

> **Language / 阅读语言 / 語言：** [简体中文](README.md) · [繁體中文](README.zh-Hant.md) · [English](README.en.md)

> Seven AI scientists walk into a review. Nobody gets to paper over the disagreements.
>
> **Try it live:** [https://poliscope.tech/](https://poliscope.tech/) — the author's public deployment; open it and start, no install needed
>
> **Paper:** [*EpistemoBrain: A Seven-Scientist Council with Dual-Graph Evidence Governance for Auditable Scientific Blindspot Discovery*](docs/paper/epistemobrain_main.pdf) (ACL Findings 2026 format; LaTeX sources in `docs/paper/`)

Poliscope is a **deep-research agent** for contested questions in computational social science: submit a dubious question, and **7 AI scientists with distinct specialties** gather evidence independently, cross-examine one another, and hunt for counterexamples, producing a **controversy evidence map where conclusions sit beside limitations, every claim traces to its source, and no dissent is deleted**.

## Core Principles

- **Not medical advice.** For mental-health questions the report explicitly states "research assistance, not clinical diagnosis."
- **No fabricated conclusions.** Missing model access, exhausted budgets, absent seats — each is marked explicitly; a blank is never "no problem."
- **Model confidence is not statistical evidence.** Model judgments, authors' own words, and statistical inference stay separately labeled.
- **Dissent is never deleted.** Refuted and quarantined positions keep their sources and remain traceable.

## Docs & Deployment

- Self-hosting (Docker Compose), full CLI / API reference, and architecture: [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md)
- Formal design specification: [`docs/superpowers/specs/2026-07-31-poliscope-design.md`](docs/superpowers/specs/2026-07-31-poliscope-design.md)
- Technical white paper (Chinese): [*Poliscope 技术白皮书*](docs/tech/tech.pdf)

## License

Poliscope's own code is open source under the [MIT License](LICENSE); the executive-memory foundation [MemoBrain](https://github.com/qhjqhj00/MemoBrain) keeps its own license — details in [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md#许可证与归属).
