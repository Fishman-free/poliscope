# Animation Plans — Poliscope Web

Audited with `improve-animations` on commit `267d543`. The motion surface
is deliberately restrained (CLAUDE.md 11: 科研仪器风格); these plans fix
the four places where it deviates from the codebase's own conventions —
composited-only animation, trigger-originated popovers, token durations,
and the 320ms token language.

## Plan index

| # | Title | Severity | Status |
|---|---|---|---|
| 001 | Tab indicator: transition transform instead of left/width | MEDIUM | TODO |
| 002 | Popovers scale from their trigger; AccountMenu popover gets an entrance | MEDIUM | TODO |
| 003 | fade-in-up entrance: 0.55s → the 320ms token | MEDIUM | TODO |
| 004 | LiveView phase highlight: box-shadow pulse → opacity fade | LOW | TODO |

## Recommended execution order

1. **003** — one-line change, highest certainty, zero risk; do first as a
   warm-up and to lock the token usage.
2. **002** — two small CSS additions; no JS; immediately visible on the
   avatar menu.
3. **001** — one CSS block rewrite; the only plan whose feel-check needs
   the Performance panel (composite-only confirmation).
4. **004** — lowest severity, purely corrective; do last.

## Dependencies

None — all four touch disjoint files (`base.css`, `SessionHistory.css` +
`AccountMenu.css`, `App.css`, `LiveView.css`) and can land in any order or
in one commit. Each plan is self-contained for a zero-context executor.

## Executing

```bash
# pick one plan and run it with any agent, e.g.
improve-animations execute plans/003-fade-in-up-duration.md
```
