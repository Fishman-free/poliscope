# MemoBrain Third-Party License Record

- **Project:** MemoBrain — Executive Memory as an Agentic Brain for Reasoning
- **Upstream URL:** https://github.com/qhjqhj00/MemoBrain
- **Paper:** https://aclanthology.org/2026.findings-acl.127/ (arXiv:2601.08079)
- **Checked commit:** `82f16e17c28313a57bf95d83340142b96507f3d1` (fetched 2026-08-28)
- **License:** **Apache License 2.0** — verified from the repository's root
  `LICENSE` file. (The upstream `pyproject.toml` still says `MIT`; that is
  stale metadata and is overridden by the LICENSE file, which is the
  authoritative grant.)
- **Copyright:** MemoBrain authors — Hongjin Qian, Zhao Cao, Zheng Liu
  (Beijing Academy of Artificial Intelligence / Renmin University of China /
  Hong Kong Polytechnic University).
- **Redistribution decision:** `vendored_source_unmodified`
  - The upstream `src/` files (`memobrain.py`, `problem_tree.py`, `schema.py`,
    `prompts.py`, `__init__.py`) are vendored byte-for-byte under
    `packages/memory/vendor/memobrain/`, together with the verbatim
    Apache-2.0 `LICENSE`.
  - **No upstream file is modified.** The Poliscope adapter
    (`packages/memory/memobrain_adapter.py`) subclasses `MemoBrain` and
    overrides only `_create_completion`, the single choke point through which
    every upstream model call flows, routing them through Poliscope's
    `ModelGateway` (per CLAUDE.md 8).
  - Apache-2.0 obligations satisfied: license text retained with the vendored
    copy, and this record provides the attribution.

## Notes

Round-16 (2026-08): upstream integration activated. The trained memory model
φ from the paper (Qwen3-4B/8B/14B fine-tunes, served via vLLM) is **not**
bundled or self-hosted; the adapter runs the upstream prompts through the
deployment's configured model via the Model Gateway. The upstream README
explicitly supports this mode: "MemoBrain can work with any LLM as the
foundation model via OpenAI-compatible API."

The memory-model outputs are registered in Poliscope's schema registry as
`MemoBrainPatch` (memory construction) and `MemoBrainFlushAndFold` (memory
management), so the calls share the same repair/quarantine machinery as every
other model call.
