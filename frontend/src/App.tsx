/**
 * SANJEEVANI — Supply Chain Resilience Command Center.
 *
 * The page is ordered to match the narrative, so a presenter can scroll and a
 * judge can follow without instruction:
 *
 *   0. Loop HUD + demo controls            — where are we?
 *   1. Intelligence (signal, watchlist)    — what happened, how trustworthy?
 *   2. Scenario & impact                   — what does it create, what is hit?
 *   3. Network map                         — where in the network?
 *   4. Recovery plan (M2 boundary)         — what does the plan propose?
 *   5. Compliance                          — is it allowed?
 *   6. Approval console                    — who authorizes it?
 *   7. Execution + receipts                — what actually happened?
 *   8. Audit                               — can we trace it?
 *   9. Learning                            — what did we learn?
 *  10. Boundaries                          — what is real?
 *
 * All state comes from the server. This component orchestrates calls and renders;
 * it never decides whether an action is permitted.
 */

import { useCallback, useEffect, useMemo, useState } from "react";

import { GovernanceRefusal, api } from "./lib/api";
import type { DemoState, NetworkSnapshot } from "./lib/types";
import { deriveLoop, money } from "./lib/format";
import { LoopStrip, SideRail, type RailLink } from "./components/LoopRail";
import { NetworkMap, collectFocus } from "./components/NetworkMap";
import {
  ImpactPanel,
  ScenarioPanel,
  SignalPanel,
  WatchlistPanel,
} from "./components/IntelligencePanels";
import {
  ApprovalConsole,
  CompliancePanel,
  ExecutionPanel,
  PlanPanel,
} from "./components/GovernancePanels";
import {
  AuditPanel,
  BoundariesPanel,
  HeroKpiRow,
  LearningPanel,
} from "./components/AuditLearningPanels";
import { Badge, EmptyState, Note, Panel, Provenance, Section, Spinner } from "./components/primitives";

const RAIL_LINKS: RailLink[] = [
  { key: "SENSE", label: "Intelligence", index: "1", anchor: "intelligence" },
  { key: "SCENARIO", label: "Scenario", index: "2", anchor: "scenario" },
  { key: "IMPACT", label: "Impact", index: "3", anchor: "impact" },
  { key: "SIMULATE", label: "Network map", index: "4", anchor: "network" },
  { key: "OPTIMIZE", label: "Recovery plan", index: "5", anchor: "plan" },
  { key: "POLICY", label: "Compliance", index: "6", anchor: "compliance" },
  { key: "APPROVE", label: "Approval", index: "7", anchor: "approval" },
  { key: "EXECUTE", label: "Execution", index: "8", anchor: "execution" },
  { key: "AUDIT", label: "Audit", index: "9", anchor: "audit" },
  { key: "LEARN", label: "Learning", index: "10", anchor: "learning" },
  // "LEARN" was reused here by mistake, which gave React two children with the
  // same key (`key={link.key}` in SideRail) and logged a duplicate-key warning
  // on every render. Boundaries is not a loop stage, so it also has no stage
  // state to look up — the unique key makes both facts true at once.
  { key: "BOUNDARIES", label: "Boundaries", index: "11", anchor: "boundaries" },
];

type Busy = null | "reset" | "run" | "decide" | "execute";

interface Toast {
  tone: "ok" | "danger" | "warn";
  title: string;
  message: string;
  detail?: Record<string, unknown>;
}

export default function App(): JSX.Element {
  const [state, setState] = useState<DemoState | null>(null);
  const [network, setNetwork] = useState<NetworkSnapshot | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [busy, setBusy] = useState<Busy>(null);
  const [toast, setToast] = useState<Toast | null>(null);
  const [horizon, setHorizon] = useState<number>(10);
  const [actorId, setActorId] = useState("meera.iyer");
  const [rationale, setRationale] = useState("Protect the biologic supply; cost is inside the tier cap.");
  const [activeAnchor, setActiveAnchor] = useState("intelligence");

  const refresh = useCallback(async () => {
    const [nextState, nextNetwork] = await Promise.all([api.state(), api.network()]);
    setState(nextState);
    setNetwork(nextNetwork);
    setLoadError(null);
  }, []);

  useEffect(() => {
    refresh().catch((error: unknown) => setLoadError(String(error)));
  }, [refresh]);

  /* ------------------------------------------------------ navigation spy */

  useEffect(() => {
    const anchors = RAIL_LINKS.map((l) => l.anchor);
    const onScroll = (): void => {
      let current = anchors[0] ?? "intelligence";
      for (const anchor of anchors) {
        const element = document.getElementById(anchor);
        if (element && element.getBoundingClientRect().top <= 160) current = anchor;
      }
      setActiveAnchor(current);
    };
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  const navigate = useCallback((anchor: string) => {
    document.getElementById(anchor)?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, []);

  /* --------------------------------------------------------------- actions */

  const act = useCallback(
    async (kind: Busy, run: () => Promise<DemoState>, success: Toast) => {
      setBusy(kind);
      setToast(null);
      try {
        const next = await run();
        setState(next);
        if (next.errors.length > 0) {
          const first = next.errors[0];
          setToast({
            tone: "warn",
            title: "Loop stopped",
            message: first?.["user_message"] ?? first?.["message"] ?? "Unknown error",
            detail: first,
          });
        } else {
          setToast(success);
        }
      } catch (error: unknown) {
        if (error instanceof GovernanceRefusal) {
          setToast({
            tone: "danger",
            title: `Refused: ${error.code}`,
            message: error.message,
            detail: error.detail,
          });
        } else {
          setToast({
            tone: "danger",
            title: "Request failed",
            message: error instanceof Error ? error.message : String(error),
          });
        }
      } finally {
        setBusy(null);
      }
    },
    [],
  );

  const onReset = useCallback(() => {
    void act("reset", async () => {
      await api.reset();
      return api.state();
    }, { tone: "ok", title: "System reset", message: "Ledger, approvals and mocks cleared. No approval survives a reset." });
  }, [act]);

  const onRun = useCallback(() => {
    void act("run", () => api.run(), {
      tone: "ok",
      title: "Scenario run",
      message: "Signals sensed, events verified, impact computed. The loop has stopped at the human gate.",
    });
  }, [act]);

  const onDecide = useCallback(
    (decision: "approve" | "reject" | "request-review") => {
      const planId = state?.plan?.plan_id;
      if (!planId) return;
      const call =
        decision === "approve"
          ? api.approve(planId, actorId, rationale)
          : decision === "reject"
            ? api.reject(planId, actorId, rationale)
            : api.requestReview(planId, actorId, rationale);
      void act("decide", () => call, {
        tone: decision === "approve" ? "ok" : "warn",
        title:
          decision === "approve"
            ? "Approved — execution authorized"
            : decision === "reject"
              ? "Rejected"
              : "Review requested",
        message:
          decision === "approve"
            ? `Recorded in the ledger. Execution authority is now held by the backend state machine.`
            : "The decision and its rationale are recorded in the append-only ledger.",
      });
    },
    [act, actorId, rationale, state?.plan?.plan_id],
  );

  const onExecute = useCallback(() => {
    const planId = state?.plan?.plan_id;
    if (!planId) return;
    void act("execute", () => api.execute(planId), {
      tone: "ok",
      title: "Executed",
      message: "IBP, TM and Ariba posted under one correlation id. Receipt and outcome recorded.",
    });
  }, [act, state?.plan?.plan_id]);

  /* ---------------------------------------------------------------- derived */

  const loop = useMemo(() => deriveLoop(state), [state]);
  const primaryEvent = state?.m1?.events[0];
  const selectedImpact = useMemo(() => {
    if (!state?.m1) return null;
    return state.m1.impacts.find((i) => i.horizon_days === horizon) ?? state.m1.primary_impact;
  }, [state?.m1, horizon]);

  const focus = useMemo(
    () =>
      collectFocus(
        network,
        selectedImpact?.affected_nodes ?? [],
        selectedImpact?.trace,
        selectedImpact?.affected_shipments ?? [],
      ),
    [network, selectedImpact],
  );

  const started = Boolean(state?.m1);
  const canExecute = state?.governance?.can_execute === true;
  const executed = Boolean(state?.execution);

  return (
    <div className="app">
      {/*
       * Keyboard users should not have to tab through the header, the loop rail
       * and the command bar to reach the panels on every navigation. The target
       * is <main id="main"> below. Styled in app.css.
       */}
      <a className="skip-link" href="#main">
        Skip to content
      </a>
      <header className="app__header">
        <div className="brand">
          <svg className="brand__mark" viewBox="0 0 32 32" aria-hidden="true">
            <circle cx="16" cy="16" r="14" fill="none" stroke="var(--accent)" strokeWidth="1.6" />
            <path d="M8 19.5l5-7 4.5 5.5L24 9" fill="none" stroke="var(--ok)" strokeWidth="2" strokeLinecap="round" />
            <circle cx="24" cy="9" r="2.6" fill="var(--warn)" />
          </svg>
          <div>
            <h1 className="brand__name">SANJEEVANI</h1>
            <span className="brand__sub">Supply chain resilience command center</span>
          </div>
        </div>

        <div className="header__spacer" />

        <div className="header__meta">
          {state && (
            <>
              <Badge tone="badge--plain" title="Scenario clock">
                clock {state.scenario_clock}
              </Badge>
              <Badge tone={state.offline ? "badge--ok" : "badge--warn"} glyph={state.offline ? "✓" : "!"}>
                {state.offline ? "offline mode" : "online"}
              </Badge>
              <Provenance kind="mocked" text="SAP mocks" />
              <Provenance kind="fixture" text="M2 fixture" />
            </>
          )}
          <button type="button" className="btn" onClick={onReset} disabled={busy !== null}>
            {busy === "reset" ? <Spinner label="Resetting" /> : "Reset"}
          </button>
          <button type="button" className="btn btn--primary" onClick={onRun} disabled={busy !== null}>
            {busy === "run" ? <Spinner label="Running" /> : "Run hero scenario"}
          </button>
          <button
            type="button"
            className="btn btn--approve"
            onClick={onExecute}
            disabled={busy !== null || !canExecute}
            title={canExecute ? "Post the approved plan to IBP, TM and Ariba" : "Requires an APPROVED plan"}
          >
            {busy === "execute" ? <Spinner label="Executing" /> : executed ? "Re-run execution" : "Execute approved plan"}
          </button>
        </div>
      </header>

      <div className="app__body">
        <SideRail links={RAIL_LINKS} active={activeAnchor} onNavigate={navigate} loop={loop} />

        <main id="main" className="app__main">
          {loadError && (
            <div className="error-banner" role="alert" style={{ marginBottom: "var(--sp-4)" }}>
              <strong>Cannot reach the backend.</strong> {loadError}
            </div>
          )}

          <Section
            number="00"
            title="Resilience loop"
            note="Sense → Verify → Understand → Scenario → Simulate → Optimize → Approve → Execute → Audit → Learn"
            id="loop"
          >
            <div className="stack">
              <LoopStrip state={state} />
              {/* The headline follows the selected horizon, so the banner and the
                  impact panel can never disagree about which future is shown. */}
              {state && <HeroKpiRow state={state} impact={selectedImpact} />}
              <Note tone="note--info" glyph="ℹ">
                <strong>SIMULATE and OPTIMIZE are owned by Member 2 and are not implemented in this
                build.</strong> They are shown as pending, and the RecoveryPlan below is supplied by a
                labelled deterministic fixture rather than by an optimizer. Everything else on this
                page is running.
              </Note>
            </div>
          </Section>

          {!started && (
            <Panel
              eyebrow="Presenter"
              title="Load the hero scenario"
              subtitle="Red Sea corridor closure + a biologics cold-chain excursion, against a 12-node synthetic pharmaceutical network."
            >
              <div className="stack">
                <div className="grid grid--3">
                  <div className="stack stack--tight">
                    <div className="small muted">1. Reset</div>
                    <p className="small secondary">
                      Clears the ledger, approval authority and SAP-shaped mocks. Nothing from a
                      previous run survives.
                    </p>
                  </div>
                  <div className="stack stack--tight">
                    <div className="small muted">2. Run</div>
                    <p className="small secondary">
                      A1 senses {6} scripted signals, A2 verifies with two rules, A3 computes the
                      impact, M2's fixture supplies a plan, A5 evaluates the rulebook — then it
                      stops and waits for a human.
                    </p>
                  </div>
                  <div className="stack stack--tight">
                    <div className="small muted">3. Approve, then execute</div>
                    <p className="small secondary">
                      One decision in the approval console authorizes one governed execution across
                      IBP, TM and Ariba.
                    </p>
                  </div>
                </div>
                <div className="row">
                  <button type="button" className="btn btn--primary" onClick={onRun} disabled={busy !== null}>
                    Run hero scenario
                  </button>
                  <span className="small muted">
                    Expected first impact: {money(3908933, { compact: true })} revenue at risk at the
                    10-day horizon.
                  </span>
                </div>
              </div>
            </Panel>
          )}

          {state?.m1 && (
            <>
              <Section number="01" title="Intelligence" note="What happened, and how trustworthy is it?" id="intelligence">
                <div className="grid grid--2-1">
                  <SignalPanel m1={state.m1} primaryEvent={primaryEvent} />
                  <WatchlistPanel m1={state.m1} />
                </div>
              </Section>

              <Section number="02" title="Scenario" note="What futures does the verified event create?" id="scenario">
                <ScenarioPanel m1={state.m1} selectedHorizon={horizon} onSelectHorizon={setHorizon} />
              </Section>

              <Section number="03" title="Network impact" note="Deterministic, reproducible, traceable" id="impact">
                {selectedImpact && <ImpactPanel impact={selectedImpact} />}
              </Section>

              <Section number="04" title="Network" note="Where in the network, and how does it propagate?" id="network">
                <Panel
                  eyebrow="12-node synthetic pharmaceutical network"
                  title="Disruption propagation"
                  subtitle={`Highlighting the ${selectedImpact?.horizon_days ?? horizon}-day scenario. Nodes and lanes outside the blast radius stay muted.`}
                  actions={<Provenance kind="deterministic" text="fixed layout" />}
                >
                  <NetworkMap network={network} focus={focus} title="Red Sea corridor closure" />
                </Panel>
              </Section>

              <Section number="05" title="Recovery plan" note="Consumed from Member 2's contract" id="plan">
                <PlanPanel state={state} />
              </Section>
            </>
          )}

          {state?.compliance && (
            <Section number="06" title="Compliance" note="Is it allowed? Answered before a human is asked." id="compliance">
              <CompliancePanel record={state.compliance} />
            </Section>
          )}

          {(state?.approval_request || state?.governance) && (
            <Section number="07" title="Human approval" note="Who holds authority? Server-enforced." id="approval">
              {state.approval_request ? (
                <ApprovalConsole
                  request={state.approval_request}
                  state={state}
                  actorId={actorId}
                  rationale={rationale}
                  busy={busy !== null}
                  onActorChange={setActorId}
                  onRationaleChange={setRationale}
                  onDecide={onDecide}
                />
              ) : (
                <Panel eyebrow="Human-in-the-loop gate" title="No approval was requested">
                  <EmptyState
                    title="Compliance blocked this plan before it reached a human"
                    hint={state.compliance?.rationale ?? "See the compliance panel above."}
                    glyph="✕"
                  />
                </Panel>
              )}
            </Section>
          )}

          {state?.execution && (
            <Section number="08" title="Execution" note="One approval, three governed posts" id="execution">
              <ExecutionPanel execution={state.execution} />
            </Section>
          )}

          {state && state.ledger.length > 0 && (
            <Section number="09" title="Audit" note="Can the decision be traced?" id="audit">
              <AuditPanel state={state} />
            </Section>
          )}

          {state?.learning && (
            <Section number="10" title="Learning" note="What differed, and what is proposed" id="learning">
              <LearningPanel learning={state.learning} />
            </Section>
          )}

          {state && (
            <Section number="11" title="Boundaries" note="What is real, deterministic, simulated and mocked" id="boundaries">
              <BoundariesPanel state={state} />
            </Section>
          )}
        </main>
      </div>

      {toast && (
        <div
          className="toast"
          role={toast.tone === "danger" ? "alert" : "status"}
          aria-live={toast.tone === "danger" ? "assertive" : "polite"}
        >
          <div className="row row--between">
            <strong>{toast.title}</strong>
            <button
              type="button"
              className="btn btn--ghost"
              style={{ padding: "2px 8px" }}
              onClick={() => setToast(null)}
              aria-label="Dismiss notification"
            >
              ✕
            </button>
          </div>
          <div className="small secondary" style={{ marginTop: 4 }}>
            {toast.message}
          </div>
          {toast.detail && Object.keys(toast.detail).length > 0 && (
            <details className="disclosure" style={{ marginTop: 8 }}>
              <summary>Technical detail</summary>
              <div className="evidence__body">
                <pre className="json">{JSON.stringify(toast.detail, null, 2)}</pre>
              </div>
            </details>
          )}
        </div>
      )}
    </div>
  );
}
