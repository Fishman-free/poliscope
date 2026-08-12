# 003 — fade-in-up entrance: 0.55s → the 320ms token

- **Status**: TODO
- **Commit**: 267d543
- **Severity**: MEDIUM
- **Category**: Easing & duration
- **Estimated scope**: 1 file, 1 line

## Problem

`.fade-in-up` — the shared panel-entrance used when NewTaskView switches
between the question and claims phases (`NewTaskView.tsx:400` and `:535`)
— runs at 0.55s, above the codebase's own UI budget. The design tokens
already define `--duration-slow: 320ms` for exactly this kind of entrance
(the same panels entered with `panel-in` at 320ms feel snappy; the
fade-in-up ones feel a beat slower). Audit rule: UI animations stay under
300ms; modals/panels 200–500ms — 550ms is outside both.

`apps/web/src/styles/base.css:187` — current:

```css
.fade-in-up {
  animation: fade-in-up 0.55s var(--ease-smooth) both;
}

@keyframes fade-in-up {
  from {
    opacity: 0;
    transform: translateY(20px);
  }
  to {
    opacity: 1;
    transform: translateY(0);
  }
}
```

## Target

`apps/web/src/styles/base.css:187` — one value change:

```css
.fade-in-up {
  animation: fade-in-up var(--duration-slow) var(--ease-smooth) both;
}
```

## Repo conventions to follow

- All other entrances use the tokens: `panel-in` at `var(--duration-slow)`
  (`App.css:463`), `vt-panel-in` likewise (`App.css:543`). This makes
  `.fade-in-up` consistent with them.
- Reduced motion: `base.css:203` caps `animation-duration` at 0.01ms under
  `prefers-reduced-motion` — panels appear instantly, opacity-only. No
  extra handling.

## Steps

1. In `apps/web/src/styles/base.css`, replace `animation: fade-in-up
   0.55s var(--ease-smooth) both;` with `animation: fade-in-up
   var(--duration-slow) var(--ease-smooth) both;`.
2. Do not touch `@keyframes fade-in-up` itself (the 20px travel is fine at
   320ms; the panel reads as a faster, crisper entrance, not a jerk).
3. No TSX changes — the class name is unchanged.

## Boundaries

- Do NOT touch `NewTaskView.tsx` or any other consumer of `.fade-in-up`.
- Do NOT change the keyframe's transform values or easing.
- Do NOT touch the `panel-in`/`vt-*` animations in `App.css`.

## Verification

- **Mechanical**: `cd apps/web && npx tsc --noEmit && npx vite build` —
  both must pass.
- **Feel check**: run the dev server, create a new task:
  - Switch between the question step and the claims step: the panel
    entrance now lands in 320ms — noticeably crisper than before, in line
    with the account-settings and re-research dialogs.
  - Compare side by side with the panel-in entrances (e.g. opening a
    session-history popover): both feel like the same motion language.
  - Toggle `prefers-reduced-motion`: the panel appears instantly.
- **Done when**: the two NewTaskView phase entrances match the 320ms token
  and feel consistent with the other panel entrances.
