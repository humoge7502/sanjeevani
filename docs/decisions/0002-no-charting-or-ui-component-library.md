# 0002 — No charting or UI component library; the network map is hand-written SVG

**Status:** Accepted
**Date:** 2026-09-15

## Context

A supply-chain command center invites dependencies. The obvious candidates were a
charting library for the KPI layer, a graph/network library for the 12-node
network map, and a component kit for panels, chips, tables and modals.

Two constraints argued against all three: the demo must be reliable on venue
hardware and possibly venue Wi-Fi, and the interface must be legible to someone
looking at a projector from across a room. Both reward a small bundle and total
control over rendering.

## Decision

**No charting library. No graph library. No UI component library. React and React
DOM only.**

The network map is a hand-written SVG component of roughly 350 lines
([`NetworkMap.tsx`](../../frontend/src/components/NetworkMap.tsx)) using a
`viewBox` and `preserveAspectRatio="xMidYMid meet"`. The design system is CSS
custom properties in [`tokens.css`](../../frontend/src/styles/tokens.css). Tables
are semantic HTML. Panels, chips, meters and badges are small local components in
[`primitives.tsx`](../../frontend/src/components/primitives.tsx).

The measured result:

| | Raw | gzip |
|---|---|---|
| `index.js` | 223 KB | **67 KB** |
| `index.css` | 26 KB | **5.8 KB** |

67 KB gzipped for the entire command center — graph, timeline, approval console and
all eleven loop stages. See [performance budget](../performance-budget.md).

## Alternatives considered

**A graph library (d3-force, cytoscape, react-flow).** Rejected, and most
seriously considered. The network is 12 nodes on 6 lanes with fixed geographic
positions. Those libraries earn their size on *layout*, and there is no layout
problem here — the node positions are known. Adopting one would have added a
dependency to solve a problem this project does not have, and given up exact
control over what a node looks like when it is blocked, affected, or both.

**A charting library (recharts, visx, chart.js).** Rejected. The KPI layer is
numbers, meters and a compliance checklist, not charts. There is no time series to
render that justifies the payload.

**A component kit (MUI, Mantine, shadcn).** Rejected, and this one was genuinely
tempting for velocity. Two reasons against: a kit's default visual language reads
as "generic SaaS dashboard" — precisely the impression the design direction
excludes — and overriding a kit to reach "SAP-grade control room" costs more than
composing the roughly ten primitives this interface needs. It also makes chips,
status semantics and risk tiers harder to keep consistent, and those are where
this product's meaning lives.

## Consequences

**Good.** A 67 KB bundle loads on anything. There is no version-drift risk between
a graph library's rendering and the SVG the accessibility layer depends on. The
`role="group"` summary, the per-node `tabIndex={0}`, the dashed vs solid strokes
for blocked vs affected, and the forced-colors behaviour are all under direct
control, which matters for a product whose claim is that the interface can be
trusted.

That control paid off directly. The first version of this map was `role="img"`
with per-node `role="button"`, which axe-core rejects as `nested-interactive`: an
image is atomic, so assistive tech may flatten the very controls that remain in the
tab order. Worse, the nodes were never buttons — they have no activation handler,
so announcing "button" promised a click that did nothing. They are now a focusable
`role="group"` region with labelled, role-free nodes. A library would have decided
that markup for us, and we would have had to argue with it.

**Bad, and admitted.** Hand-written SVG is code that has to be maintained. Adding a
13th node type, an edge-weight visualisation, or zoom and pan is real work, where a
library would give some of it away. If the network grew past roughly 50 nodes or
needed force-directed layout, this decision would be wrong and should be revisited.

**Also bad.** No Storybook, so components are documented by this design system doc
and their own source rather than an interactive catalogue.

## Enforcement

- Bundle size is a budget in [performance-budget.md](../performance-budget.md)
  (≤ 120 KB gzip). A charting or graph library would blow it, which makes the
  decision self-enforcing at build time.
- `frontend/package.json` lists React, React DOM and dev tooling. Any new runtime
  dependency is a deliberate act that would show up in review.

## Revisiting this

Any of these justifies reopening it:

1. The network exceeds ~50 nodes, or needs layout the code cannot compute.
2. A genuine time-series requirement appears (trend, multi-horizon comparison
   over time) that meters cannot carry.
3. The team grows and component reuse across *multiple* applications becomes real
   — a kit amortises differently across several products than within one.

Absent one of those, the bundle budget is the tiebreaker: it is a stated,
measured constraint, and this decision is what keeps the build under it.
