# 002 — Popovers scale from their trigger; AccountMenu popover gets an entrance

- **Status**: TODO
- **Commit**: 267d543
- **Severity**: MEDIUM
- **Category**: Physicality & origin
- **Estimated scope**: 2 files, ~8 lines

## Problem

Two trigger-anchored popovers in the app bar don't follow the "scale from
the trigger" rule — the entire repo has only two `transform-origin`
declarations, both in `landing/Landing.css` for decorative lines, so every
popover currently scales from its own center.

`apps/web/src/views/SessionHistory.css:59-68` — the only popover with an
entrance animation at all:

```css
  animation: popover-in var(--duration-standard) var(--ease-smooth);
}

@keyframes popover-in {
  from {
    opacity: 0;
    transform: translateY(-6px) scale(0.98);
  }
  to {
    opacity: 0;
    transform: none;
  }
}
```

This popover is `right: 0` anchored (line 45) and slides down from its
trigger (`translateY(-6px)`), yet the scale part of the entrance happens
around the element's center — the panel reads as appearing from the middle
of the screen instead of from its trigger.

`apps/web/src/views/AccountMenu.css:41-54` — the avatar menu popover has
**no entrance animation at all**; it pops in instantly:

```css
.account-menu__popover {
  position: absolute;
  right: 0;
  top: calc(100% + 8px);
  min-width: 160px;
  ...
}
```

## Target

Both popovers scale from the top-right (their anchor is the top-right of a
right-aligned trigger) and the avatar menu gets the same entrance the
session-history popover already has.

`apps/web/src/views/SessionHistory.css` — add `transform-origin` to the
popover element (the rule that carries `animation: popover-in ...`):

```css
  animation: popover-in var(--duration-standard) var(--ease-smooth);
  transform-origin: top right;
```

`apps/web/src/views/AccountMenu.css` — add entrance + origin to
`.account-menu__popover`:

```css
.account-menu__popover {
  position: absolute;
  right: 0;
  top: calc(100% + 8px);
  min-width: 160px;
  padding: var(--space-2);
  background: var(--surface-0);
  border: 1px solid var(--line);
  border-radius: var(--radius-3);
  box-shadow: var(--shadow-2);
  z-index: 40;
  display: flex;
  flex-direction: column;
  gap: 2px;
  animation: popover-in var(--duration-standard) var(--ease-smooth);
  transform-origin: top right;
}
```

## Repo conventions to follow

- Cross-file keyframe reuse is already established: `ReResearchDialog.css`
  and `AccountSettings.css` both animate with `panel-in`, which is defined
  in `App.css`. `popover-in` is defined in `SessionHistory.css` and is
  globally available to `AccountMenu.css` the same way.
- Tokens: `--duration-standard` (200ms), `--ease-smooth` — already used by
  the source animation, keep them.
- Reduced motion: `base.css:203` caps `animation-duration` to 0.01ms under
  `prefers-reduced-motion` — both popovers will appear instantly there,
  which is the codebase's chosen reduced-motion behaviour. No extra
  handling.

## Steps

1. In `apps/web/src/views/SessionHistory.css`, add `transform-origin: top
   right;` to the popover element rule that declares `animation:
   popover-in ...` (immediately after the `animation` line).
2. In `apps/web/src/views/AccountMenu.css`, add the `animation` and
   `transform-origin` lines to `.account-menu__popover` as shown in Target.

## Boundaries

- Do NOT touch the popover positioning (`right: 0`, `top: calc(100% +
  8px)`) or any markup.
- Do NOT create a new keyframe — reuse `popover-in` verbatim.
- Do NOT touch `SessionHistory.css` beyond the one added declaration.

## Verification

- **Mechanical**: `cd apps/web && npx tsc --noEmit && npx vite build` —
  both must pass.
- **Feel check**: run the dev server:
  - Click the session-history button: the popover scales down from its
    top-right corner toward center as it fades in — the origin feels like
    the trigger, not the screen middle.
  - Click the avatar: the account menu now fades/scale-in with the same
    motion instead of popping.
  - In DevTools Animations panel, slow playback to 10%: the scale origin
    sits at the top-right corner of both popovers.
  - Toggle `prefers-reduced-motion`: both appear instantly, no movement.
- **Done when**: both popovers visibly emerge from their trigger corner and
  reduced-motion shows them instantly.
