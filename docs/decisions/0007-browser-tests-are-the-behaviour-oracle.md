# 0007 — Real Chromium is the behaviour and accessibility oracle

**Status:** Accepted
**Date:** 2026-09-15

## Context

Before this decision the project had 134 backend tests and 38 frontend unit tests,
and **no browser had ever rendered the interface**. The frontend typechecked, built
to 67 KB gzipped, and had a static accessibility guard that asserted CSS was
declared. None of that is evidence that the product works.

The concrete risk was stated plainly in the project's own review: the UI had never
been seen. A typechecker cannot tell you a button is unreachable, a table cannot be
scrolled, a colour pair is illegible, or a React key collides. Those failures are
invisible to every tool the project was using.

## Decision

Add a Playwright suite, `frontend/e2e/`, that boots the **real backend and the real
frontend** on isolated ports (8788 / 5174) and drives the product in Chromium. It
runs on two viewports — 1600 px and 1120 px — and is split into three concerns:

- `hero.spec.ts` — the full journey, and determinism across a second run.
- `governance.spec.ts` — bypass attempts at the **HTTP boundary**, not just the
  Python API. Forged approve/execute calls are issued from the page context.
- `a11y.spec.ts` — axe-core across WCAG 2.1 A/AA, plus keyboard, focus,
  reduced-motion and responsive behaviour.

Two supporting choices matter as much as the suite itself:

**Ports are deliberately not the demo ports.** `scripts/dev.sh` serves 8787/5173.
Copying those into the test config would mean the suite silently exercised whatever
dev server happened to be running instead of the code under test.

**`data-state` on the loop strip is the wait target, not visible text.** The stage
labels are upper-cased by CSS, so `getByText("AWAITING HUMAN")` also matched
`awaiting human approval` elsewhere on the page. Tests now read a machine-readable
attribute. The same reasoning added `data-present` to the audit coverage rows.

## What it found on first run

Nine failures, five of them genuine product defects:

| Defect | Why nothing else caught it |
|---|---|
| `--text-muted` at 4.43:1 and `--text-faint` at 2.62:1 on the panel surface | Contrast is a computed property of the rendered pixel, not of the source |
| Network map `role="img"` containing 12 `role="button"` nodes | Semantics only conflict once the accessibility tree is built |
| Learning scorecard table unscrollable by keyboard | Requires a layout pass to detect overflow |
| Loop HUD overflows 1120 px; stages 11–12 unreachable by keyboard | Only appears at a narrow viewport |
| Duplicate React `key="LEARN"` in the side rail | A runtime warning, never a build error |

The other four were test premises that were simply wrong — including one that
revealed a real UX contradiction: the headline KPI always reported the 10-day
primary impact, so selecting the 30-day horizon left a banner reading "10-day
horizon" directly above a panel reading "30 days". The headline now follows the
selected horizon.

## Consequence: the risky upgrade became safe

The frontend toolchain had five advisories, one of them critical
(`vitest` ≤ 4.1.10). Clearing them required `vite` 5 → 7, `vitest` 2 → 5 and
`@vitejs/plugin-react` 4 → 5.2, i.e. three major versions immediately before a
demo.

That upgrade was only defensible because the browser suite existed to prove it had
not broken anything. It was performed one major at a time, and every step was
checked against the same 48 browser tests plus typecheck, unit tests and build. The
advisories are now at zero.

Two notes for whoever repeats this:

1. `npm install vitest@5` fails on npm 10.8.2 with `Cannot read properties of null
   (reading 'edgesOut')` — a resolver bug triggered by vitest 5's long list of
   optional peer dependencies. `--legacy-peer-deps` resolves it; the resulting tree
   dedupes cleanly on a single `vite@7.3.6`.
2. `@vitejs/plugin-react@6` requires `vite@8` **and** the oxc/rolldown transform
   chain. `5.2.0` supports vite 7 and 8 and avoids that migration.

## Alternatives considered

**jsdom plus Testing Library.** Rejected as the *primary* layer. jsdom has no layout
engine and no accessibility tree, so it cannot catch the five defects above — it
would have been a fifth tool that reaffirms what Vitest already checks. Something
has to render pixels, and it may as well be the browser the demo runs in.

**Manual review in a browser.** Rejected. It found none of these five, and it does
not run on every commit.

**Adding axe-core.** Accepted, and it earned its place: it is the only thing in the
project that can check colour contrast, and contrast is a claim this design system
explicitly makes. One dev dependency for the entire WCAG A/AA rule set is a good
trade. It is worth being precise that axe proves the *absence* of certain failures,
not accessibility.

## Consequences

**Good.** The product has been rendered. The accessibility claims are verified
rather than asserted. Backend governance is now attacked at the HTTP boundary as
well as the Python boundary. A toolchain three majors deep has a regression net.

**Bad, and admitted.** A Chromium download (~150 MB) is now required for a full
`verify.sh`, so the browser step SKIPS with an explicit message when it is absent
rather than reporting a false pass. The suite adds ~1.6 minutes.

**Also bad.** Browser tests are slower and more brittle than unit tests, and they
must run serially because the backend holds one mutable demo state. Every spec
therefore resets first, which is correct but makes each one slightly slower.

**Coverage is not complete.** There is no visual-regression diffing, no
screenshot baseline, and the `chromium-narrow` project runs the same specs as the
desktop project against a different viewport rather than testing narrow-specific
behaviour exhaustively.

## Enforcement

- `scripts/verify.sh` step 6/7 runs the suite.
- The a11y spec fails the build on any serious or critical WCAG violation — the
  contraction this project's palette forced is recorded in
  [0008](0008-text-ramp-anchored-to-measured-contrast.md).
- `npm --prefix frontend run test:browser:headed` runs it with a visible browser,
  which is the recommended way to rehearse before presenting.

## Revisiting this

Reopen if CI cannot afford the browser download (consider a prebuilt image), or if
the suite's runtime passes ~5 minutes (split by concern and parallelise the
read-only specs).
