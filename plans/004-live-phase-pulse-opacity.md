# 004 — LiveView phase highlight: box-shadow pulse → opacity fade

- **Status**: TODO
- **Commit**: 267d543
- **Severity**: LOW
- **Category**: Performance
- **Estimated scope**: 1 file, ~10 lines

## Problem

The current-phase chip in the live view announces a phase change with a
320ms `box-shadow` ring that collapses to nothing. Box-shadow is not a
composited property — the ring repaints every frame of the animation (a
blurred, semi-transparent expanding ring is among the most expensive
repaints a small element can do), on the app's most animation-heavy screen
(the live process view streams token deltas).

`apps/web/src/views/LiveView.css:41-50` — current:

```css
.live__phase--current {
  color: var(--surface-0);
  border-color: var(--accent);
  background: var(--accent);
  animation: phase-current-in var(--duration-slow) var(--ease-smooth);
}

@keyframes phase-current-in {
  from {
    box-shadow: 0 0 0 5px var(--accent-soft);
  }
  to {
    box-shadow: 0 0 0 0 transparent;
  }
}
```

The semantic of the ring is "this phase just became current — watch it".
Opacity fades carry that meaning on a composited path, which is what the
rest of the codebase animates.

## Target

`apps/web/src/views/LiveView.css` — same duration and easing, opacity only:

```css
@keyframes phase-current-in {
  from {
    opacity: 0.55;
  }
  to {
    opacity: 1;
  }
}
```

(The element already has an `animation` declaration and the accent
background; the fade from 55% to full opacity reads as "the current phase
lights up", on the GPU-composited path.)

## Repo conventions to follow

- Every other animation in the app animates `transform`/`opacity` only
  (`panel-in`, `popover-in`, `fade-in-up`, `vt-*`). This brings
  `phase-current-in` in line.
- Tokens unchanged: `var(--duration-slow)`, `var(--ease-smooth)`.
- Reduced motion: `base.css:203` caps `animation-duration` at 0.01ms —
  the chip just appears, no pulse. No extra handling.

## Steps

1. In `apps/web/src/views/LiveView.css`, replace the
   `@keyframes phase-current-in` block (lines 44-50) with the Target
   block.
2. Leave `.live__phase--current` and its `animation:` declaration
   untouched — only the keyframe body changes.

## Boundaries

- Do NOT touch `.live__phase--current` (its background/border/color stay —
   the accent chip already carries the state change).
- Do NOT touch any other LiveView animation.
- Do NOT add `transform` to the keyframe — the chip must not move; it sits
   in a horizontal phase rail.

## Verification

- **Mechanical**: `cd apps/web && npx tsc --noEmit && npx vite build` —
  both must pass.
- **Feel check**: run the dev server with a live task (or the demo), watch
  a phase change:
  - The newly-current phase chip fades in over 320ms — the state change is
    still obvious, just without the repaint-heavy ring.
  - Compare before/after in DevTools Rendering → Paint flashing: the
    phase-change frame now paints only the chip, not a growing blur ring.
  - Toggle `prefers-reduced-motion`: the chip appears instantly.
- **Done when**: phase changes read clearly with no box-shadow paint.
