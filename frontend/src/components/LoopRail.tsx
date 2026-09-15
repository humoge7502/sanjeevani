/**
 * The loop rail: the eleven-stage narrative as a navigable table of contents.
 *
 * Two jobs, one component:
 *   1. Answer "where are we in the story?" at a glance (the sticky loop strip).
 *   2. Let a presenter jump to any surface without hunting (the side rail).
 *
 * States are derived from server state only (see lib/format.deriveLoop). The
 * frontend never decides whether a stage is permitted.
 */

import type { DemoState, LoopState } from "../lib/types";
import { LOOP_STAGES, deriveLoop, type LoopStatus } from "../lib/format";

const STATE_GLYPH: Record<LoopState, string> = {
  done: "✓",
  active: "▶",
  awaiting: "!",
  blocked: "✕",
  pending: "○",
};

const STATE_TEXT: Record<LoopState, string> = {
  done: "Done",
  active: "Running",
  awaiting: "Awaiting human",
  blocked: "Blocked",
  pending: "Pending",
};

export function loopClassName(status: LoopStatus): string {
  return `loop__stage loop__stage--${status.state}`;
}

export function LoopStrip({ state }: { state: DemoState | null }): JSX.Element {
  const loop = deriveLoop(state);
  return (
    /*
     * tabIndex is load-bearing, not decoration. Twelve stages at a 92px
     * minimum overflow a 1120px viewport, so this strip really does scroll —
     * and a scroll container that is not focusable can only be scrolled with a
     * pointer. Keyboard users would simply never see stages 11 and 12. This is
     * the `scrollable-region-focusable` rule, and it only appears at narrow
     * widths, which is exactly why it survived a wide-viewport review.
     */
    <div className="loop" role="list" aria-label="Resilience loop stages" tabIndex={0}>
      {LOOP_STAGES.map((stage) => {
        const status: LoopStatus = loop[stage.key] ?? { state: "pending", note: "" };
        return (
          <div
            key={stage.key}
            role="listitem"
            /*
             * `data-state` is the machine-readable form of the stage status.
             * The visible state is text plus a glyph, but the text is
             * case-transformed by CSS, which makes it an unreliable thing to
             * assert on. This attribute is what the browser tests read.
             */
            data-testid={`loop-stage-${stage.key}`}
            data-state={status.state}
            className={`${loopClassName(status)}${stage.human ? " loop__stage--human" : ""}`}
            title={`${stage.label} — ${status.note}`}
          >
            <span className="loop__code">
              {stage.code} · {stage.owner}
            </span>
            <span className="loop__name">{stage.label}</span>
            <span className="loop__state">
              <span aria-hidden="true">{STATE_GLYPH[status.state]}</span>
              {STATE_TEXT[status.state]}
            </span>
          </div>
        );
      })}
    </div>
  );
}

export interface RailLink {
  key: string;
  label: string;
  index: string;
  anchor: string;
}

export function SideRail({
  links,
  active,
  onNavigate,
  loop,
}: {
  links: RailLink[];
  active: string;
  onNavigate: (anchor: string) => void;
  loop?: Record<string, LoopStatus>;
}): JSX.Element {
  return (
    <nav className="rail" aria-label="Surface navigation">
      <p className="rail__title">Journey</p>
      <ul className="rail__list">
        {links.map((link) => {
          const stage = loop?.[link.key];
          return (
            <li key={link.key}>
              <button
                type="button"
                className="rail__link"
                aria-current={active === link.anchor}
                onClick={() => onNavigate(link.anchor)}
              >
                <span className="rail__index" aria-hidden="true">
                  {link.index}
                </span>
                <span>{link.label}</span>
                {stage && (
                  <span
                    className="badge badge--plain"
                    title={stage.note}
                    aria-label={`${link.label}: ${STATE_TEXT[stage.state]}`}
                  >
                    <span aria-hidden="true">{STATE_GLYPH[stage.state]}</span>
                  </span>
                )}
              </button>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
