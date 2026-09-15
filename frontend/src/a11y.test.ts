/**
 * Accessibility guard.
 *
 * `docs/design-system.md` claims a set of accessibility properties. A claim in a
 * document decays; a claim in a test does not. This file reads the source and
 * fails if any of those properties is removed.
 *
 * These are STATIC assertions over source. They are the fast layer, not the
 * authority: `frontend/e2e/a11y.spec.ts` renders the real product in Chromium
 * and runs axe-core over the WCAG 2.1 A/AA rule set, which is the only way to
 * check colour contrast, layout overflow or the computed accessibility tree.
 * This file catches structural regressions in milliseconds; the browser suite
 * catches what only exists once the page is rendered. Both are needed, and the
 * five defects the browser suite found are listed in docs/design-system.md.
 *
 * These are deliberately narrow. Each assertion targets something that would be
 * silently lost in a refactor and that a reviewer might not notice:
 * removing the focus ring, unwrapping a table's semantic header, or turning a
 * keyboard-reachable graph node back into a decorative shape.
 */

/**
 * Remove comments before asserting.
 *
 * This matters more than it looks. These guards match raw source text, and a
 * comment is source text: when the network map's markup was corrected, the
 * assertions for `role="button"` and `role="img"` kept passing because those
 * exact strings still appeared — inside the comments explaining their removal.
 * A guard satisfied by prose is not a guard.
 *
 * Only `/* * /` blocks and whole-line `//` comments are stripped, so a URL such
 * as `https://…` inside a string literal is never mangled.
 */
function stripComments(source: string): string {
  return source.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");
}

const APP_CODE = stripComments(APP);
const LOOP_RAIL_CODE = stripComments(LOOP_RAIL);
const NETWORK_MAP_CODE = stripComments(NETWORK_MAP);
const PRIMITIVES_CODE = stripComments(PRIMITIVES);

import { describe, expect, it } from "vitest";

// Vite's `?raw` suffix hands back the file's source as a string, which is
// declared by `vite/client` (already in tsconfig `types`). Using it avoids
// adding `@types/node` purely to read files in a test.
import APP from "./App.tsx?raw";
import AUDIT from "./components/AuditLearningPanels.tsx?raw";
import GOVERNANCE from "./components/GovernancePanels.tsx?raw";
import INTELLIGENCE from "./components/IntelligencePanels.tsx?raw";
import LOOP_RAIL from "./components/LoopRail.tsx?raw";
import NETWORK_MAP from "./components/NetworkMap.tsx?raw";
import PRIMITIVES from "./components/primitives.tsx?raw";
import APP_CSS from "./styles/app.css?raw";
import TOKENS from "./styles/tokens.css?raw";

/** Every source file, for whole-tree assertions. */
const ALL_SOURCES: ReadonlyArray<readonly [string, string]> = [
  ["./App.tsx", APP],
  ["./components/AuditLearningPanels.tsx", AUDIT],
  ["./components/GovernancePanels.tsx", GOVERNANCE],
  ["./components/IntelligencePanels.tsx", INTELLIGENCE],
  ["./components/LoopRail.tsx", LOOP_RAIL],
  ["./components/NetworkMap.tsx", NETWORK_MAP],
  ["./components/primitives.tsx", PRIMITIVES],
];

describe("keyboard access", () => {
  it("provides a skip link to the main content region", () => {
    expect(APP_CSS).toContain(".skip-link");
    // It must be revealed on focus, or it is useless to the people who need it.
    expect(APP_CSS).toContain(".skip-link:focus");
    expect(APP).toMatch(/className="skip-link"/);
  });

  it("keeps a visible focus ring and never removes it globally", () => {
    expect(APP_CSS).toContain(":focus-visible");
    expect(APP_CSS).toContain("outline: 2px solid var(--accent)");
    // `outline: none` is the classic accessibility regression. It appears once,
    // on the map, where a ring is replaced by a dedicated node highlight.
    const bareOutlines = APP_CSS.match(/outline:\s*none/g) ?? [];
    expect(bareOutlines.length).toBeLessThanOrEqual(1);
    expect(APP_CSS).toContain(".netmap__node:focus-visible");
  });

  it("makes graph nodes reachable by keyboard, without claiming they are buttons", () => {
    // A node that is only hoverable is invisible to keyboard users, so tabIndex
    // and a per-node label must stay.
    expect(NETWORK_MAP_CODE).toContain("tabIndex={0}");
    expect(NETWORK_MAP_CODE).toMatch(/aria-label=\{`\$\{node\.name\}/);

    // But they must NOT be buttons. They have no activation handler, so
    // role="button" announced a click that did nothing — and, nested inside a
    // role="img" parent, it was an axe `nested-interactive` violation.
    expect(NETWORK_MAP_CODE).not.toContain('role="button"');
  });

  it("keeps every scrollable region reachable by keyboard", () => {
    // `overflow: auto` is pointer-only unless the container can take focus.
    // axe reports this as `scrollable-region-focusable`; it is invisible to
    // source review because it only exists once layout has run.
    expect(PRIMITIVES_CODE).toMatch(/className="table__scroll"[\s\S]{0,200}?tabIndex=\{0\}/);
    expect(LOOP_RAIL_CODE).toMatch(/className="loop"[\s\S]{0,200}?tabIndex=\{0\}/);
  });

  it("gives every side-rail link a unique React key", () => {
    // A duplicate key is a runtime React warning, never a build error, so it
    // survived typecheck, unit tests and review. "LEARN" was used twice.
    const keys = [...APP_CODE.matchAll(/\{\s*key:\s*"([A-Z_]+)",\s*label:/g)].map(
      (match) => match[1],
    );
    expect(keys.length).toBeGreaterThan(8);
    expect(new Set(keys).size, `duplicate key in ${keys.join(", ")}`).toBe(keys.length);
  });
});

describe("screen-reader semantics", () => {
  it("exposes the network map as a labelled group with a textual summary", () => {
    // SVG is opaque to assistive technology without a role and a label. It
    // must be a GROUP rather than an image: an image is atomic, so assistive
    // tech may flatten the focusable nodes it still contains.
    expect(NETWORK_MAP_CODE).toContain('role="group"');
    expect(NETWORK_MAP_CODE).toMatch(/aria-label=\{`Supply network map/);
    expect(NETWORK_MAP_CODE).not.toContain('role="img"');
  });

  it("declares the loop rail and the map legend as lists", () => {
    expect(LOOP_RAIL_CODE).toContain('role="list"');
    expect(LOOP_RAIL_CODE).toContain('role="listitem"');
    expect(NETWORK_MAP_CODE).toContain('role="list"');
    expect(NETWORK_MAP_CODE).toContain('role="listitem"');
  });

  it("gives every data table a caption and scoped headers", () => {
    for (const [path, source] of [
      ["./components/IntelligencePanels.tsx", INTELLIGENCE],
      ["./components/AuditLearningPanels.tsx", AUDIT],
    ] as const) {
      expect(source, `${path} must caption its table`).toContain(
        '<caption className="visually-hidden">',
      );
      expect(source, `${path} must scope its headers`).toContain('scope="col"');
      // A caption styled by a class that does not exist would be visible text.
      expect(APP_CSS).toContain(".visually-hidden");
    }
  });

  it("announces errors and status changes to assistive technology", () => {
    // `role="alert"` interrupts; `role="status"` is polite. Both are needed:
    // a governance refusal must be heard, a count update must not interrupt.
    expect(APP).toContain('role="alert"');
    expect(PRIMITIVES).toContain('role="status"');
    expect(PRIMITIVES).toContain('role="meter"');
  });

  it("labels horizon and navigation control groups", () => {
    expect(INTELLIGENCE).toMatch(/role="group" aria-label="Scenario horizon"/);
    expect(LOOP_RAIL_CODE).toMatch(/aria-label="Resilience loop stages"/);
  });

  it("labels the scroll regions it makes focusable", () => {
    // A focusable box with no name is worse than no focus target at all: a
    // screen reader announces an anonymous stop.
    expect(PRIMITIVES_CODE).toMatch(/role="region"[\s\S]{0,80}?aria-label=\{label\}/);
  });
});

describe("colour independence", () => {
  it("honours forced-colors by falling back to system canvas and text", () => {
    expect(TOKENS).toContain("@media (forced-colors: active)");
    expect(TOKENS).toContain("CanvasText");
    expect(TOKENS).toContain("--bg-panel: Canvas");
  });

  it("pairs each semantic status with a border token, not a fill alone", () => {
    // A colour-only status is unreadable for a colourblind user and in
    // forced-colors mode. The token set requires a border companion per status,
    // and the components carry a glyph and a text label alongside it.
    for (const status of ["ok", "warn", "danger", "info", "violet"]) {
      expect(TOKENS, `--${status}-border must exist`).toContain(`--${status}-border:`);
      expect(TOKENS, `--${status}-dim must exist`).toContain(`--${status}-dim:`);
    }
  });

  it("maps risk tiers onto the existing status ramp rather than a new one", () => {
    // Reusing the ramp means a reader learns one colour language, not two.
    expect(TOKENS).toContain("--tier-l1: var(--ok)");
    expect(TOKENS).toContain("--tier-l4: var(--danger)");
  });
});

describe("motion", () => {
  it("collapses all motion under prefers-reduced-motion", () => {
    expect(TOKENS).toContain("@media (prefers-reduced-motion: reduce)");

    // Every duration token must be neutralised, not just some of them.
    const motionBlock = TOKENS.slice(
      TOKENS.indexOf("@media (prefers-reduced-motion: reduce)"),
    );
    for (const token of ["--dur-instant", "--dur-fast", "--dur-normal", "--dur-slow"]) {
      expect(motionBlock, `${token} must collapse`).toMatch(
        new RegExp(`${token}:\\s*1ms`),
      );
    }

    // And it must apply to animations the token set does not own.
    expect(motionBlock).toContain("animation-duration: 1ms !important");
    expect(motionBlock).toContain("transition-duration: 1ms !important");
  });
});

describe("injection safety", () => {
  it("never renders untrusted content as HTML", () => {
    // The backend carries seeded news headlines and advisory bodies. If any of
    // that ever reached `dangerouslySetInnerHTML`, a hostile feed entry would
    // become script execution in the operator's console.
    for (const [path, source] of ALL_SOURCES) {
      expect(source, `${path} must not inject HTML`).not.toContain(
        "dangerouslySetInnerHTML",
      );
      expect(source, `${path} must not assign innerHTML`).not.toContain("innerHTML");
      expect(source, `${path} must not use eval`).not.toMatch(/\beval\s*\(/);
    }
  });
});
