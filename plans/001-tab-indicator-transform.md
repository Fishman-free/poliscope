# 001 — Tab indicator: transition transform instead of left/width

- **Status**: TODO
- **Commit**: 267d543
- **Severity**: MEDIUM
- **Category**: Performance
- **Estimated scope**: 1 file, ~10 lines

## Problem

The top-bar tab indicator glides between tabs by animating `left` and
`width` — both layout properties, so every tab switch triggers layout +
paint + composite on the main thread. This is the highest-frequency
animated element in the app (navigation, tens of times per day).

`apps/web/src/App.css:444-450` — current:

```css
.app__tab-indicator {
  position: absolute;
  bottom: -1px;
  height: 2px;
  border-radius: var(--radius-pill);
  background: var(--app-accent);
  left: var(--indicator-left, 0px);
  width: var(--indicator-width, 0px);
  transition: left var(--duration-slow) var(--ease-smooth),
    width var(--duration-slow) var(--ease-smooth);
}
```

The JavaScript that feeds it already documents the intent — the code
comment says "App.css transitions the transform" but the CSS never did:

`apps/web/src/App.tsx:314-319`:

```ts
  // Sliding tab indicator: measure the active tab so the bar glides from
  // wherever it was to the new position (App.css transitions the transform).
```

The fix is to make the CSS match the comment: the indicator keeps its
position via `transform: translateX(...)` (composited, GPU-friendly) and
lets the width change instantly — in a 320ms glide the eye tracks the
position, not the width, so the width snap is imperceptible.

## Target

`apps/web/src/App.css` — the indicator block becomes:

```css
.app__tab-indicator {
  position: absolute;
  bottom: -1px;
  left: 0;
  height: 2px;
  border-radius: var(--radius-pill);
  background: var(--app-accent);
  width: var(--indicator-width, 0px);
  transform: translateX(var(--indicator-left, 0px));
  transition: transform var(--duration-slow) var(--ease-smooth);
}
```

## Repo conventions to follow

- Easing/duration tokens come from `apps/web/src/styles/tokens.css`
  (`--duration-slow: 320ms`, `--ease-smooth: cubic-bezier(0.22, 1, 0.36, 1)`)
  — already used here, keep them.
- Reduced motion needs no extra work: `base.css:203` whitelists
  `transition-property` to opacity/color under `prefers-reduced-motion`,
  which excludes transform — the indicator snaps instead of gliding, which
  is exactly the "drop the travel" behaviour the codebase already chose.

## Steps

1. In `apps/web/src/App.css`, replace the `.app__tab-indicator` block
   (lines 444-450, quoted above) with the Target block. Note the two
   changes: `left: var(...)` becomes `left: 0` + `transform:
   translateX(var(...))`; the transition animates `transform` only.
2. Leave `apps/web/src/App.tsx` untouched — it already sets
   `--indicator-left` / `--indicator-width` in pixels (line 858-859), which
   translateX consumes directly.

## Boundaries

- Do NOT touch `apps/web/src/App.tsx` (the variable names stay, the values
  stay pixel offsets).
- Do NOT add `transition: width` back — the width snap during the glide is
  intentional.
- Do NOT change the `app__tab-hint` responsive rules in the same file.

## Verification

- **Mechanical**: `cd apps/web && npx tsc --noEmit && npx vite build` —
  both must pass. `npx eslint src/App.css` if configured, else nothing.
- **Feel check**: run the dev server, click tabs:
  - The indicator glides smoothly between tabs, exactly as before — the
    change is invisible to the eye at 320ms.
  - Rapidly click across tabs: the glide retargets from the current
    position (CSS transitions retarget; never restart from zero).
  - In DevTools Performance panel, record a tab switch: no
    Layout/Paint entries attributable to the indicator (only Composite).
  - Toggle `prefers-reduced-motion` (Rendering panel): the indicator
    snaps instantly instead of gliding, and the active tab is still
    clearly marked.
- **Done when**: indicator glides via composite-only, reduced-motion snaps,
  and the comment at `App.tsx:316` now matches reality.
