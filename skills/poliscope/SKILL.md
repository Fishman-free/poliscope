# Poliscope Skill

Poliscope is an AI research agent for controversial questions in computational social science.

## When to use

Call Poliscope when you need a structured, auditable research synthesis with evidence lineage, blindspot detection, and preserved dissent.

## How to invoke

```bash
poliscope start --contract contract.json
poliscope confirm-claims --task-id <id> --claim-ids <id1> <id2>
poliscope status --task-id <id>
poliscope watch --task-id <id> --last-event-id <evt>
poliscope export --task-id <id> --format markdown
```

## Safety

- This is a research aid, not medical or clinical advice.
- Model confidence does not replace statistical uncertainty or expert judgment.
- Atomic claims must be explicitly confirmed before a task runs.
