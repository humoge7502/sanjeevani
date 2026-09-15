# Design system

The design system exists for one reason: **so that a judge can understand the
situation in five seconds and the presenter never has to explain the interface.**

Everything below is implemented, not aspirational. Token values are quoted from
[`frontend/src/styles/tokens.css`](../frontend/src/styles/tokens.css) so the
document and the code cannot drift silently.

---

## 0. What was studied, and what was not

The brief named `acmvit.in` as a reference to analyse. It was fetched (HTTP 200)
and inspected on 2026-09-15. **The honest scope of that inspection:**

- It is a **client-rendered** student-chapter and community site, so text
extraction returned only a fraction of the page — a project showcase
(ExamCooker) and the chapter's framing copy. The rendered component tree, CSS and
animation code were **not** inspectable as text.
- **No source, stylesheet, asset or copy was copied.** Only a small amount of
served text was readable, and it is a different product category entirely.

**[INFERENCE]** What the readable material demonstrated is not a visual technique
but a communication posture: lead with *what the project does for the user*, in
plain language, before any technology claims. A project card says what ExamCooker
is and who it helps.

**Where that principle was applied.** The README opens with the loop in one
sentence rather than an architecture diagram. The hero scenario names the
consignment, the market and the money at risk before any KPI grid. The loop rail
labels each stage with a plain-language verb. The one place a technical claim leads
is
[`mock-boundaries.md`](mock-boundaries.md) — and it leads with a confession, not a
capability.

**What was deliberately not inherited.** The site's marketing register — large
hero imagery, project-advertising tone, celebratory copy — is wrong here. This is
a control room, and its register is clinical. Being a different kind of product is
not a failure to learn from the reference; it is the point of the analysis.

**What could not be assessed.** Responsive behaviour under real interaction,
motion design, and component patterns require a browser with a rendered DOM. None
was available in this environment. Nothing is claimed about them.

---

## 1. Design direction

**"SAP-grade enterprise control room."**

Named explicitly because knowing what to avoid is most of the work:

| Not this | Because |
|---|---|
| Generic SaaS dashboard | Judges see dozens; the shape stops carrying meaning. |
| Marketing landing page | There is no user to convert. The user is already here, under time pressure. |
| Cyberpunk AI demo | Glow and motion read as gimmick, and gimmick reads as untrustworthy — the opposite of the product's claim. |
| Fake SAP Fiori clone | Cosplay. It would imply an integration that does not exist. |
| A wall of cards | Equal visual weight for unequal information is a hierarchy failure. |

The interface has to earn trust, because its job is to hand a human a
consequential decision. Every choice below is filtered through that.

---

## 2. Colour

Dark, low-chroma surfaces with **high chroma reserved for meaning**. A dark canvas
is not a stylistic preference here — a control room under pressure benefits from
reduced glare, and saturated colour then reads unambiguously as signal.

Six deliberate surface steps, not an ad-hoc pile:

```css
--bg-base:   #080b11;   /* app background        */
--bg-canvas: #0b1017;   /* content region        */
--bg-panel:  #111823;   /* panel                 */
--bg-panel-2:#161f2c;   /* nested panel          */
--bg-raised: #1c2735;   /* raised / hover        */
--bg-inset:  #070a0f;   /* wells, code, receipts */
```

Text ramp — four steps, each **measured** above 4.5:1 against the lightest
surface it is ever drawn on (`--bg-raised`, `#1c2735`):

```css
--text:           #e8eef7;   /* primary   — 14.9:1 */
--text-secondary: #a8b8cc;   /* labels    —  7.5:1 */
--text-muted:     #8fa0b7;   /* metadata  —  5.7:1 */
--text-faint:     #8496ad;   /* disabled  —  5.0:1 */
```

These values are not the original ones. The first palette used `#6f8098` and
`#4d5c72`, which measured **4.43:1** and **2.62:1** on the panel surface — both
below the floor, on the tokens that carry most of this dense interface. axe-core
found it in a real browser; no amount of source review would have. The floor is now
mechanically enforced by `e2e/a11y.spec.ts`, and the trade-off — a visibly
compressed ramp, since "faint" cannot be very faint on near-black and still be
legible — is recorded in [ADR 0008](decisions/0008-text-ramp-anchored-to-measured-contrast.md).

Semantic status — each with a `-dim` fill and a `-border` companion so a chip can
be built without inventing a value:

```css
--ok: #2ea66b    --warn: #d8a326    --danger: #f0553f
--info: #39c0d4  --violet: #a077f0  --accent: #3d8bfd
```

**Risk tiers map onto the status ramp rather than defining a parallel one:**

```css
--tier-l1: var(--ok);       /* routine        */
--tier-l2: var(--accent);   /* planned        */
--tier-l3: var(--warn);     /* elevated       */
--tier-l4: var(--danger);   /* consequential  */
```

This is the load-bearing colour decision. Risk is the axis the whole governance
story turns on, so it reuses the ramp a reader has already learned from status
chips instead of asking them to learn a second one.

### Colour is never the only carrier of meaning

Stated in the token file itself and enforced in the components. Every status chip
pairs a colour with **a glyph and a text label**; the risk tier renders as `L3`
plus a word; blocked vs affected on the network map differ by stroke style
(dashed vs solid) as well as hue. The `forced-colors` media query collapses fills
to `Canvas`/`CanvasText`, so the interface survives high-contrast mode on borders
and text alone.

---

## 3. Typography

Inter for UI, system monospace for anything a human might compare character by
character — ids, hashes, correlation ids, rule codes.

```css
--font-ui:   "Inter", -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
--font-mono: ui-monospace, "SF Mono", "JetBrains Mono", Menlo, Consolas, monospace;
```

Eight steps on a restrained scale, each with a stated purpose:

| Token | Size | Purpose |
|---|---|---|
| `--fs-2xs` | 11px | Micro labels — **uppercase only**, never running text |
| `--fs-xs` | 12px | Secondary metadata |
| `--fs-sm` | 13px | Table cells, dense body |
| `--fs-md` | 14px | Body default |
| `--fs-lg` | 16px | Panel titles |
| `--fs-xl` | 20px | Section headings |
| `--fs-2xl` | 28px | KPI values |
| `--fs-3xl` | 36px | Hero KPI (revenue at risk, one per view) |

`--tracking-wide` (0.08em) exists specifically because uppercase micro labels at
11px are illegible without positive tracking. Three weights carry the hierarchy:
regular, medium, semibold. Bold is reserved for numerical emphasis.

---

## 4. Space, radius, elevation

A 4px base scale, nine steps: `--sp-1` (4px) → `--sp-12` (48px). No component
invents a spacing value.

Five radii, each with a role: `--r-xs` 3px (chips), `--r-sm` 5px (inputs),
`--r-md` 8px (panels), `--r-lg` 12px (dialogs), `--r-full` (pills).

Four elevation levels, expressed as dark-background shadow spreads rather than
light-mode drop shadows, so raised surfaces separate from the canvas without a
halo:

```css
--e1: 0 1px 2px rgba(0,0,0,.32)     --e3: 0 8px 24px rgba(0,0,0,.44)
--e2: 0 2px 8px rgba(0,0,0,.36)     --e4: 0 16px 48px rgba(0,0,0,.5)
```

---

## 5. Motion

Four durations on two easings, and **every one has a semantic job**:

| Token | Value | Job |
|---|---|---|
| `--dur-instant` | 90ms | Press feedback, chip state |
| `--dur-fast` | 160ms | Hover, focus ring |
| `--dur-normal` | 260ms | Panel entry, stage transition |
| `--dur-slow` | 480ms | The execution sequence — three SAP actions in order |

```css
--ease-out:    cubic-bezier(0.16, 1, 0.3, 1);      /* entering  */
--ease-in-out: cubic-bezier(0.65, 0, 0.35, 1);     /* state change */
```

**What is deliberately absent.** No particles, no ambient movement, no animated
background, no looping animation on the network map. The graph is static until the
data changes, because a moving target is a harder thing to read under pressure —
and because continuous animation is what makes a demo feel like a toy.

The one place motion is used generously is the moment the product is actually
being sold: the three-step SAP execution. Sequencing IBP → TM → Ariba over
`--dur-slow` makes the *fan-out* — one approval becoming three governed actions —
visible as a sequence rather than asserted in a status list. That is motion
carrying meaning, which is the bar.

### Reduced motion is a first-class path

`prefers-reduced-motion: reduce` collapses every duration token to 1ms and forces
`animation-duration` / `transition-duration` on all elements. The demo remains
completely readable, because motion was never the only carrier of information —
the same rule the colour system follows.

---

## 6. Layout

```css
--rail-w: 232px;    /* loop rail — always visible, never a drawer */
--maxw: 1680px;     /* content ceiling */
--header-h: 52px;
```

The loop rail is fixed and always present. That is a hierarchy decision: the
eleven-stage loop is the product's thesis, and putting it behind a hamburger would
hide the one thing that makes the architecture legible.

---

## 7. Component vocabulary

Reusable primitives live in
[`primitives.tsx`](../frontend/src/components/primitives.tsx) and
[`app.css`](../frontend/src/styles/app.css). The rule is that a concept the
product has gets exactly one component:

`KPI` · `StatusChip` · `RiskTierBadge` · `Panel` · `Meter` (`role="meter"`) ·
`EvidenceList` · `ProvenanceTag` · `StepList` · `ReceiptStep` · `RootCauseCard`

Two consequences worth naming:

- **`ProvenanceTag` is a component, not a label.** Every compliance check renders
  one, which is how "configured policy" vs "simulated rule" vs "illustrative
  check" survives into the UI instead of being lost in the backend. It is backed
  by `test_every_check_carries_a_provenance_label_and_config_key`.
- **`ReceiptStep` sequences the three SAP actions**, so the fan-out is rendered by
  one component in one place and a fourth mock could not be added inconsistently.

---

## 8. Accessibility

Implemented, and verified by inspection of the source rather than asserted from
memory.

| Practice | Where |
|---|---|
| Skip link to main content | `.skip-link` — `app.css:38`, revealed on `:focus` |
| Visible focus ring, never removed | `:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px }` — `app.css:32` |
| Semantic tables with header scope and captions | `IntelligencePanels.tsx:486+`, `AuditLearningPanels.tsx:325+` |
| Screen-reader-only table captions | `.visually-hidden` — `app.css:55` |
| Keyboard-reachable graph nodes | `tabIndex={0}` with a descriptive `aria-label` per node, and **no** widget role — `NetworkMap.tsx` |
| Graph exposed as a labelled group with a textual summary | `role="group"` plus an `aria-label` naming affected nodes — `NetworkMap.tsx` |
| Scroll regions reachable and named | `TableScroll` renders `role="region"` + `aria-label` + `tabIndex={0}` — `primitives.tsx` |
| The loop HUD is scrollable by keyboard | `className="loop" … tabIndex={0}` — `LoopRail.tsx` |
| Lists declared as lists | `role="list"` / `role="listitem"` on the loop rail and map legend |
| Errors announced | `role="alert"` on the error banner — `App.tsx:287` |
| Meters exposed as meters | `role="meter"` — `primitives.tsx:206` |
| Status changes announced politely | `role="status"` — `primitives.tsx:350` |
| Reduced motion honoured | `tokens.css` — all durations collapse to 1ms |
| High contrast honoured | `forced-colors: active` reassigns surface and border tokens |
| Non-colour status encoding | Glyph + text label on every chip; dash vs solid strokes on the map |

### The guards are enforced, not just documented

The table above is not a claim that decays in a document. It is enforced in **two layers** — 16 static source assertions in the normal
`npm run test` run, and a real-browser suite that renders the product and runs
axe-core. They check different things and both are needed.

### Layer 1 — static source assertions (`a11y.test.ts`, 16 guards)

| Guard | Fails if someone… |
|---|---|
| Skip link + its focus rule | removes the `.skip-link` `/` `.skip-link:focus` pair, or the anchor in `App.tsx` |
| Focus ring | deletes `:focus-visible`, or adds global `outline: none` |
| Keyboard graph nodes | drops `tabIndex` or the per-node `aria-label` — or reintroduces `role="button"` on nodes that cannot be activated |
| Map text alternative | drops `role="group"` or its summary label |
| Focusable scroll regions | drops `tabIndex={0}` from `.table__scroll` or `.loop` |
| Named scroll regions | drops `role="region"` or `aria-label` from the scroll wrapper |
| Unique rail keys | reuses a `key` across the side-rail links |
| Table semantics | removes a `<caption>` or `scope="col"` |
| Live regions | removes `role="alert"` / `role="status"` |
| Group labels | removes the horizon group's `aria-label` |
| Reduced motion | removes `prefers-reduced-motion`, or collapses only *some* durations |
| Forced colours | removes the `forced-colors` token fallbacks |
| Colour independence | removes a status `-border`/`-dim` companion, or breaks the risk-tier alias |
| Injection safety | introduces `dangerouslySetInnerHTML`, `innerHTML` or `eval` into any component |

This layer found a real defect on first run: `.skip-link` was styled in `app.css`
and `<main id="main">` existed, but **the anchor itself was never rendered** — the
CSS was dead and the skip link did nothing. That is exactly the failure mode a
document cannot catch.

**A caveat about this layer, learned the hard way.** These guards match raw source
text, and a comment is source text. When the network map's markup was corrected,
the assertions for `role="button"` and `role="img"` *kept passing* — because
those exact strings still appeared inside the comments explaining their removal. A
guard satisfied by prose is not a guard, so `stripComments()` now removes `/* */`
blocks and whole-line `//` comments before any assertion runs.

### Layer 2 — the rendered product (`e2e/a11y.spec.ts`)

A static assertion cannot tell you a colour is illegible, a table cannot be
scrolled, or the accessibility tree is malformed. It checks that CSS is
*declared*; it cannot check the page is *usable*.

So [`frontend/e2e/a11y.spec.ts`](../frontend/e2e/a11y.spec.ts) boots the real
backend and frontend and runs **axe-core** across the WCAG 2.1 A/AA rule set on
three rendered states — initial, populated and executed — at two viewports. It
fails the build on any serious or critical violation.

**What it found that no other check could:**

| Violation | Detail |
|---|---|
| `color-contrast` | `--text-muted` at 4.43:1 and `--text-faint` at 2.62:1 on the panel surface — below the 4.5:1 floor, across KPI labels, table headers, policy check IDs and the audit timeline. Fixed; see [ADR 0008](decisions/0008-text-ramp-anchored-to-measured-contrast.md). |
| `nested-interactive` | The map was `role="img"` holding 12 focusable nodes — an atomic container with children still in the tab order. |
| `scrollable-region-focusable` | The learning scorecard table overflows horizontally with no keyboard way to scroll it. |
| `scrollable-region-focusable` | At 1120 px the 12-stage loop HUD overflows, so stages 11 and 12 were unreachable by keyboard. **Only reproducible at a narrow viewport** — which is why the suite runs two. |

**Honest limitation.** axe-core proves the *absence* of the failures it knows how
to detect. It is not a substitute for testing with a real screen reader, and this
project has not done that. VoiceOver, NVDA and TalkBack remain unverified. The
contrast ratios, keyboard reachability and computed roles *are* verified in a real
rendering engine; the experience of listening to the page is not.

---

## 9. Responsive behaviour

The layout is built for the room it will be presented in: a single wide screen.

- Primary target: 1280–1680px.
- Panels reflow to a single column below the rail breakpoint.
- Tables scroll horizontally rather than compressing to illegibility.
- The network map scales via SVG `viewBox` + `preserveAspectRatio="xMidYMid meet"`,
  so it never distorts.

**Honest limitation.** A phone is not a target. This is a control room for a
planner at a workstation, and a 90-second demo of an eleven-stage governed
execution is not a phone experience. Mobile is out of scope by decision, not by
oversight.

---

## 10. Error, empty and offline states

The happy path is not the only path that was built:

| State | Treatment |
|---|---|
| Loading / busy | Buttons disable with `busy !== null`; no optimistic UI lies about progress |
| Empty | Panels render an explanatory empty state, never a blank region |
| Governance refusal | Legible 4xx with a code, a human sentence and a detail object — surfaced as a toast, never a bare 500 |
| Blocked by policy | The blocking checks render in the compliance panel with their failing values |
| Rejected | A distinct terminal state with the actor and rationale recorded |
| Partial execution | Replayed steps are marked, so a retry is visually distinguishable from a fresh run |
| Offline | Stated as a property of the system, not presented as a degraded mode — offline is the default |
