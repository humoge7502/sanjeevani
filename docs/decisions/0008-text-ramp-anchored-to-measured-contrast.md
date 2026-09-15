# 0008 — The text ramp is anchored to a measured contrast floor, not to taste

**Status:** Accepted
**Date:** 2026-09-15

## Context

The original palette was chosen visually. It was a dark, low-chroma ramp with four
text steps, and it looked correct on screen:

```css
--text:           #e8eef7;
--text-secondary: #9fb0c6;
--text-muted:     #6f8098;
--text-faint:     #4d5c72;
```

The third and fourth steps carried a great deal of the interface. This product is
deliberately dense — KPI labels and footnotes, table headers, policy check IDs, the
audit timeline's timestamps and metadata, provenance strings. Almost all of it is
small type in `--text-muted` or `--text-faint`.

When axe-core first ran against the rendered page it reported `color-contrast` as
**serious**, and not on one element — on well over a hundred, spanning every panel
in the product. The measured values against `--bg-panel` (`#111823`):

| Token | Original | Contrast on panel | WCAG AA (4.5:1) |
|---|---|---|---|
| `--text-secondary` | `#9fb0c6` | 8.06:1 | pass |
| `--text-muted` | `#6f8098` | **4.43:1** | **fail, narrowly** |
| `--text-faint` | `#4d5c72` | **2.62:1** | **fail, badly** |

## Decision

**Every step of the text ramp must clear 4.5:1 against the lightest surface it is
ever drawn on.** For this palette that surface is `--bg-raised` (`#1c2735`), which
is a stricter bar than the panel background the failures were measured on.

```css
--text:           #e8eef7;   /* 14.9:1 on --bg-raised */
--text-secondary: #a8b8cc;   /*  7.5:1 */
--text-muted:     #8fa0b7;   /*  5.7:1 */
--text-faint:     #8496ad;   /*  5.0:1 */
```

The check is mechanical, not visual: `frontend/e2e/a11y.spec.ts` runs axe-core on
three rendered states — initial, populated and executed — and fails on any serious
or critical violation. A future palette edit that drops below the floor fails the
build with the offending selector printed.

## The honest consequence

**The ramp is now compressed, and that is a real loss.** `--text-faint` went from
`#4d5c72` to `#8496ad` — much lighter — and it now sits close to `--text-muted`.
The original four-step ramp had real expressive range; the accessible one has less.

There is no clever way out of this. On a near-black canvas, text below roughly
4.5:1 is *by definition* hard to read, which is the entire point of the threshold.
A designer can either have a faint tier that is genuinely faint, or one that
complies. This project chose compliance, because the interface's central claim is
that a human can audit the numbers on it — and an unreadable footnote on a
$3.9M exposure figure is not a stylistic choice, it is a defect.

The mitigation is hierarchy rather than dimness: size, weight and grouping carry
the de-emphasis that lightness used to. `--text-secondary` at 7.5:1 still reads
clearly as a step above `--text-muted`.

## Alternatives considered

**Keep the palette, relax the rule to 3:1.** Rejected. 3:1 is the large-text
threshold, and this interface uses this colour almost exclusively on small type.
Applying the large-text bar to 11 px labels would be choosing the rule that passes
rather than the rule that fits.

**Raise only `--text-faint`.** Rejected, and this is the interesting near-miss:
`--text-muted` failed by 0.07 at 4.43:1, which is exactly the kind of margin that
gets waved through as measurement noise. It is not noise. It is a fail, and it
appeared on more elements than any other token.

**Lighten the panel surfaces instead, keeping the text.** Considered, and it would
have preserved the ramp. Rejected because it weakens the dark, calm control-room
surface that the whole visual direction depends on, and the failure was on the text
side, not the surface side.

**Use a slightly lighter surface only behind muted text.** Rejected as
unmaintainable: it makes legibility depend on every future component remembering
which background it sits on.

## Consequences

**Good.** The contrast claim in the design system is now measured and enforced
rather than asserted. All four tiers are uniform, so a component author cannot
accidentally pick a failing one. The failures were found by tooling, before a judge
saw them on a projector.

**Bad, and admitted.** Less tonal range, and the palette is visibly less "moody"
than the first version. `--text-faint` no longer does much that `--text-muted`
cannot.

**Also bad.** The reported ratios are computed against `--bg-raised`, the lightest
surface in the ramp. If a future component introduces a lighter surface, the
floor moves and the tokens must be re-checked. The axe run will catch that, but
only for states the spec renders — the three states it covers are initial,
populated and executed, which is not the same as every possible view.

## Revisiting this

Reopen only with a mechanism that makes the faint tier conditional on context — for
example, raising the surface behind it — or if `--bg-raised` stops being the
lightest surface. Do not reopen merely to restore the old values; the measurement
is the argument.
