/**
 * Audit, learning and boundary surfaces.
 *
 * The audit view exists to prove the system is not a black box. It renders the
 * decision timeline in the exact order the loop ran, with the actor, the result
 * and the hash-chain link for every record — including the records that represent
 * a refusal or a deduction, not just the successes.
 */

import type {
  DemoState,
  GovernancePayload,
  ImpactPayload,
  LearningPayload,
} from "../lib/types";
import {
  OUTCOME_GLYPH,
  humaniseKey,
  clockTime,
  moneyExact,
  money,
  num,
  percent,
  signedMoney,
  signedPercent,
  shorten,
} from "../lib/format";
import {
  Badge,
  Bar,
  DefList,
  Disclosure,
  EmptyState,
  Evidence,
  Kpi,
  Note,
  Panel,
  Provenance,
  StatRow,
  StatusBadge,
  TableScroll,
} from "./primitives";

/* ------------------------------------------------------------------ audit */

function timelineDotClass(result: string): string {
  const value = result.toUpperCase();
  if (["FAILED", "FAIL", "BLOCKED", "REJECTED", "DENIED"].includes(value)) {
    return "timeline__dot timeline__dot--blocked";
  }
  if (["REVIEW", "WARN", "PARTIAL"].includes(value)) {
    return "timeline__dot timeline__dot--review";
  }
  if (["DEDUPLICATED", "SKIPPED"].includes(value)) {
    return "timeline__dot timeline__dot--dedup";
  }
  return "timeline__dot";
}

export function AuditPanel({ state }: { state: DemoState }): JSX.Element {
  const { ledger } = state;
  const coverage = ledger.coverage;
  const chain = ledger.chain;

  return (
    <Panel
      eyebrow="A6 Audit"
      title="Decision ledger"
      subtitle="Append-only, hash-chained, ordered. Every stage of the loop, including the refusals."
      actions={
        <div className="row">
          <Badge tone={chain.intact ? "badge--ok" : "badge--danger"} glyph={chain.intact ? "✓" : "✕"}>
            {chain.intact ? "chain intact" : "chain broken"}
          </Badge>
          <Badge tone="badge--plain">{ledger.length} records</Badge>
        </div>
      }
    >
      <div className="stack">
        <div className="grid grid--4">
          <Kpi label="Ledger records" value={ledger.length} foot="append-only" accent="var(--accent)" />
          <Kpi
            label="Loop stages covered"
            value={`${coverage.stages_present.length}/${coverage.stages_expected.length}`}
            foot={coverage.complete ? "complete" : `missing ${coverage.stages_missing.join(", ")}`}
            accent={coverage.complete ? "var(--ok)" : "var(--warn)"}
          />
          <Kpi
            label="Chain head"
            value={<span className="mono" style={{ fontSize: "var(--fs-sm)" }}>{shorten(chain.head, 16)}</span>}
            foot="SHA-256 over the record body"
            accent="var(--violet)"
          />
          <Kpi
            label="Determinism"
            value={<span style={{ fontSize: "var(--fs-lg)" }}>frozen clock</span>}
            foot="re-running the demo reproduces this chain"
            accent="var(--info)"
          />
        </div>

        <Note tone="note--info" glyph="ℹ">
          <strong>What the hash chain does and does not do.</strong> {chain.scope} Real tamper
          resistance needs append-only storage and external anchoring — stated as a production
          requirement rather than implied by a hash column.
        </Note>

        <div className="grid grid--2-1">
          <div className="panel panel--flush">
            <div className="panel__head">
              <div className="panel__titles">
                <p className="panel__eyebrow">Chronological decision trail</p>
                <h4 className="panel__title" style={{ fontSize: "var(--fs-md)" }}>
                  What happened, in order
                </h4>
              </div>
            </div>
            <div className="panel__body">
              {ledger.timeline.length === 0 ? (
                <EmptyState title="Ledger is empty" hint="Run the scenario to populate it." />
              ) : (
                <ol className="timeline">
                  {ledger.timeline.map((entry) => (
                    <li className="timeline__item" key={entry.seq}>
                      <div className="timeline__time">{clockTime(entry.timestamp)}</div>
                      <div className="timeline__spine">
                        <span className={timelineDotClass(entry.result)} aria-hidden="true" />
                      </div>
                      <div style={{ minWidth: 0 }}>
                        <div className="row row--between">
                          <span className="timeline__stage">
                            {entry.seq}. {entry.stage.replace(/_/g, " ")}
                          </span>
                          <span className="pill-row">
                            {OUTCOME_GLYPH[entry.result] && (
                              <Badge tone={entry.result === "OK" ? "badge--ok" : "badge--plain"} glyph={OUTCOME_GLYPH[entry.result]}>
                                {entry.result}
                              </Badge>
                            )}
                          </span>
                        </div>
                        <div className="timeline__action">
                          <span className="mono">{entry.action}</span> · {entry.actor}
                        </div>
                        {Object.keys(entry.ids).length > 0 && (
                          <div className="timeline__meta">
                            {Object.entries(entry.ids).map(([k, v]) => (
                              <span key={k} className="mono" style={{ marginRight: 8 }}>
                                {k}={v}
                              </span>
                            ))}
                          </div>
                        )}
                        <div className="tiny muted mono" style={{ marginTop: 2 }}>
                          hash {entry.hash} ← prev {entry.prev_hash}
                        </div>
                      </div>
                    </li>
                  ))}
                </ol>
              )}
            </div>
          </div>

          <div className="stack">
            <Panel eyebrow="Coverage" title="Stage coverage">
              <div className="stack stack--tight">
                {coverage.stages_expected.map((stage) => {
                  const present = coverage.stages_present.includes(stage);
                  return (
                    <div
                      className="row row--between"
                      key={stage}
                      data-testid={`coverage-${stage}`}
                      data-present={present}
                    >
                      <span className="small mono">{stage.replace(/_/g, " ")}</span>
                      <Badge tone={present ? "badge--ok" : "badge--plain"} glyph={present ? "✓" : "○"}>
                        {present ? "recorded" : "absent"}
                      </Badge>
                    </div>
                  );
                })}
              </div>
            </Panel>

            <Panel eyebrow="Chain verification" title="Hash chain">
              <div className="stack stack--tight">
                <StatRow label="Records" value={chain.records} />
                <StatRow label="Chain enabled" value={chain.chain_enabled ? "yes" : "no"} />
                <StatRow label="Intact" value={chain.intact ? "yes" : "NO — tampering detected"} />
                {chain.issues.length > 0 && (
                  <Note tone="note--danger" glyph="✕">
                    <pre className="json">{JSON.stringify(chain.issues, null, 2)}</pre>
                  </Note>
                )}
                <StatRow label="Head" value={shorten(chain.head, 20)} />
              </div>
            </Panel>

            <GovernanceHistoryPanel governance={state.governance} />
          </div>
        </div>

        <Evidence title="Full ledger records (raw JSONL shape)" data={ledger.timeline} />
      </div>
    </Panel>
  );
}

export function GovernanceHistoryPanel({
  governance,
}: {
  governance: GovernancePayload | null;
}): JSX.Element {
  if (!governance) {
    return (
      <Panel eyebrow="State machine" title="Governance history">
        <EmptyState title="No plan under governance" glyph="◇" />
      </Panel>
    );
  }
  return (
    <Panel
      eyebrow="Approval state machine"
      title="Governance transitions"
      actions={<StatusBadge status={governance.state} />}
    >
      <div className="stack stack--tight">
        <StatRow label="Risk tier" value={governance.risk_tier} />
        <StatRow label="Required role" value={governance.required_role ?? "—"} />
        <StatRow label="Can execute" value={governance.can_execute ? "YES" : "no"} />
        <StatRow label="Blocks execution" value={governance.blocks_execution ? "yes" : "no"} />
        {governance.blocked_reason && (
          <Note tone="note--danger" glyph="✕">
            {governance.blocked_reason}
          </Note>
        )}
        <div className="divider" />
        <ol className="timeline">
          {governance.history.map((h) => (
            <li className="timeline__item" key={h.seq}>
              <div className="timeline__time">{clockTime(h.timestamp)}</div>
              <div className="timeline__spine">
                <span
                  className={
                    h.to === "REJECTED" || h.to.includes("BLOCKED")
                      ? "timeline__dot timeline__dot--blocked"
                      : "timeline__dot"
                  }
                  aria-hidden="true"
                />
              </div>
              <div>
                <div className="small mono">
                  {h.from} → <strong>{h.to}</strong>
                </div>
                <div className="tiny muted">
                  by {h.actor}
                  {h.actor_role ? ` (${h.actor_role})` : ""} — {h.reason}
                </div>
              </div>
            </li>
          ))}
        </ol>
        {governance.review_notes.length > 0 && (
          <>
            <div className="divider" />
            <div className="small muted">Review notes</div>
            {governance.review_notes.map((n, i) => (
              <div className="small secondary" key={i}>
                <span className="mono">{n.by}</span> ({n.role}): {n.note}
              </div>
            ))}
          </>
        )}
      </div>
    </Panel>
  );
}

/* --------------------------------------------------------------- learning */

export function LearningPanel({ learning }: { learning: LearningPayload }): JSX.Element {
  const scorecard = learning.scorecard;
  const outcome = learning.outcome;
  const maxAbs = Math.max(
    ...scorecard.rows.map((r) => Math.max(Math.abs(r.predicted), Math.abs(r.actual))),
    1,
  );

  return (
    <Panel
      eyebrow="A6 Learning"
      title="Predicted vs actual"
      subtitle={outcome.learning_note}
      actions={
        <div className="row">
          <Badge tone="badge--violet" glyph="◈">
            no retraining
          </Badge>
          <Badge tone="badge--plain">{scorecard.summary.metrics_compared} metrics</Badge>
        </div>
      }
    >
      <div className="stack">
        <div className="grid grid--4">
          <Kpi label="Metrics compared" value={scorecard.summary.metrics_compared} foot="predicted vs observed" accent="var(--accent)" />
          <Kpi label="Better than predicted" value={scorecard.summary.better_than_predicted} foot="favourable deltas" accent="var(--ok)" />
          <Kpi label="Worse than predicted" value={scorecard.summary.worse_than_predicted} foot="unfavourable deltas" accent="var(--warn)" />
          <Kpi
            label="Calibration signals"
            value={scorecard.summary.calibration_signals}
            foot="proposed for human review"
            accent="var(--violet)"
          />
        </div>

        <Note tone="note--info" glyph="ℹ">
          <strong>What "learning" means here.</strong> The outcome is observed, the delta is
          calculated and a calibration signal is emitted. No model is retrained, and{" "}
          <span className="mono">retraining_performed</span> is hard-wired to{" "}
          <span className="mono">false</span>. Claims of autonomous retraining would be false.
        </Note>

        <div className="panel panel--flush">
          <div className="panel__head">
            <div className="panel__titles">
              <p className="panel__eyebrow">Scorecard · {scorecard.strategy}</p>
              <h4 className="panel__title" style={{ fontSize: "var(--fs-md)" }}>
                Predicted / actual / delta
              </h4>
            </div>
          </div>
          <TableScroll label="Predicted versus actual outcome per metric">
            <table className="table">
              <caption className="visually-hidden">
                Predicted versus actual outcome per metric
              </caption>
              <thead>
                <tr>
                  <th scope="col">Metric</th>
                  <th scope="col" className="num">
                    Predicted
                  </th>
                  <th scope="col" className="num">
                    Actual
                  </th>
                  <th scope="col" className="num">
                    Delta
                  </th>
                  <th scope="col" className="num">
                    %
                  </th>
                  <th scope="col">Comparison</th>
                  <th scope="col">Direction</th>
                </tr>
              </thead>
              <tbody>
                {scorecard.rows.map((row) => {
                  const tone =
                    row.direction === "better"
                      ? "badge--ok"
                      : row.direction === "worse"
                        ? "badge--warn"
                        : "badge--plain";
                  const glyph = row.direction === "better" ? "▲" : row.direction === "worse" ? "▼" : "=";
                  const isMoney = row.metric.includes("usd") || row.metric.includes("cost");
                  const isRatio = ["service_level", "temperature_risk", "resilience_score", "stockout_probability", "service_level_risk"].includes(
                    row.metric,
                  );
                  const fmt = (v: number) => (isRatio ? num(v, 4) : isMoney ? money(v, { compact: true }) : num(v, 2));
                  return (
                    <tr key={row.metric}>
                      <td>{humaniseKey(row.metric)}</td>
                      <td className="num">{fmt(row.predicted)}</td>
                      <td className="num">{fmt(row.actual)}</td>
                      <td className="num">{isRatio ? num(row.absolute_delta, 4) : isMoney ? signedMoney(row.absolute_delta) : num(row.absolute_delta, 2)}</td>
                      <td className="num">{signedPercent(row.percent_delta)}</td>
                      <td style={{ minWidth: 120 }}>
                        <div className="waterfall__track">
                          <div
                            className="waterfall__bar waterfall__bar--predicted"
                            style={{ width: `${(Math.abs(row.predicted) / maxAbs) * 100}%` }}
                          />
                          <div
                            className="waterfall__bar waterfall__bar--actual"
                            style={{
                              width: `${(Math.abs(row.actual) / maxAbs) * 100}%`,
                              top: "50%",
                              bottom: "auto",
                              height: "4px",
                            }}
                          />
                        </div>
                        <div className="tiny muted" style={{ marginTop: 2 }}>
                          <span className="mono">— predicted</span> ·{" "}
                          <span className="mono">▬ actual</span>
                        </div>
                      </td>
                      <td>
                        <Badge tone={tone} glyph={glyph}>
                          {row.direction}
                        </Badge>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </TableScroll>
          <div className="panel__body">
            <p className="tiny muted">{scorecard.method}</p>
          </div>
        </div>

        <div className="grid grid--2">
          <Panel eyebrow="Calibration" title="Signals proposed for review">
            <div className="stack stack--tight">
              {learning.calibration_signals.length === 0 && (
                <EmptyState title="No material deviation" hint="All deltas were within tolerance." glyph="✓" />
              )}
              {learning.calibration_signals.map((signal) => (
                <div className="note" key={signal.key}>
                  <span className="note__glyph" aria-hidden="true">
                    ◈
                  </span>
                  <div>
                    <div className="small">{signal.observation}</div>
                    <div className="tiny muted">{signal.proposal}</div>
                    <div className="row" style={{ marginTop: 4 }}>
                      <Badge tone={signal.requires_human_approval ? "badge--warn" : "badge--plain"}>
                        {signal.requires_human_approval ? "human approval required" : "informational"}
                      </Badge>
                      <Badge tone="badge--plain">auto-applied: {String(signal.auto_applied)}</Badge>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </Panel>

          <Panel eyebrow="Observation source" title="Where actuals came from">
            <div className="stack stack--tight">
              <Provenance kind={String(learning.observation_source["labelled_as"] ?? "simulated")} />
              <DefList
                rows={Object.entries(learning.observation_source)
                  .filter(([k]) => !["labelled_as"].includes(k))
                  .map(([k, v]): [string, JSX.Element] => [
                    humaniseKey(k),
                    <span className="mono break small">{String(v)}</span>,
                  ])}
              />
              <Note tone="note--warn" glyph="⚠">
                These observations are seeded file data, not live feedback from TM, IBP or QA. The
                seam is documented so a real deployment can populate it.
              </Note>
            </div>
          </Panel>
        </div>

        <Evidence title="Outcome contract payload" data={outcome} />
      </div>
    </Panel>
  );
}

/* -------------------------------------------------------------- boundaries */

export function BoundariesPanel({ state }: { state: DemoState }): JSX.Element {
  const boundaries = state.mock_boundaries;
  const provider = state.plan_provider;

  const groups: Array<[string, string, string[]]> = [
    ["real", "Real", boundaries.real],
    ["deterministic", "Deterministic", boundaries.deterministic],
    ["simulated", "Simulated", boundaries.simulated],
    ["mocked", "Mocked", boundaries.mocked],
  ];

  return (
    <Panel
      eyebrow="Honesty"
      title="What is real, deterministic, simulated and mocked"
      subtitle="The boundary inventory. A judge should be able to check every claim against this list."
    >
      <div className="stack">
        <div className="grid grid--2">
          {groups.map(([kind, label, items]) => (
            <div key={kind} className="panel panel--flush">
              <div className="panel__head">
                <div className="panel__titles">
                  <h4 className="panel__title" style={{ fontSize: "var(--fs-md)" }}>
                    {label} <Provenance kind={kind} />
                  </h4>
                </div>
              </div>
              <div className="panel__body">
                <ul className="small secondary" style={{ margin: 0, paddingLeft: 18 }}>
                  {items.map((item) => (
                    <li key={item}>{item}</li>
                  ))}
                </ul>
              </div>
            </div>
          ))}
        </div>

        <div className="grid grid--2">
          <Panel eyebrow="Explicitly not built" title="Out of scope for this MVP">
            <ul className="small secondary" style={{ margin: 0, paddingLeft: 18 }}>
              {boundaries.not_implemented.map((item) => (
                <li key={item}>{item}</li>
              ))}
            </ul>
          </Panel>
          <Panel eyebrow="AI usage" title="Where models are and are not used">
            <p className="small secondary">{boundaries.ai_usage}</p>
            <div className="divider" />
            <StatRow label="Backend LLM enabled" value={state.offline ? "false (offline default)" : "false"} />
            <StatRow label="M2 provider" value={provider.provider} />
            <StatRow label="M2 optimizer implemented" value={provider.optimizer_implemented ? "yes" : "no"} />
            <StatRow label="Fixture version" value={provider.fixture_version ?? "—"} />
            <StatRow label="Offline mode" value={state.offline ? "on" : "off"} />
            <StatRow label="SAP real integration" value={state.sap.real_integration ? "yes" : "no"} />
          </Panel>
        </div>

        <Disclosure title="M2 integration contract (what M3 expects from Member 2)">
          <DefList
            rows={[
              ["Provider", <span className="mono">{provider.provider}</span>],
              ["Owner", <span className="mono">{provider.owner}</span>],
              ["Consumed by", <span className="mono">{provider.consumed_by}</span>],
              ["Reality", <span className="mono">{provider.reality}</span>],
              ["Disclosure", <span className="small break">{provider.disclosure}</span>],
            ]}
          />
          <div className="divider" />
          <p className="small secondary">
            M3 calls exactly two methods:{" "}
            <span className="mono">plan_for_event(event_id, scenario_id)</span> and{" "}
            <span className="mono">ranked_plans(event_id, scenario_id)</span>, both returning the
            frozen <span className="mono">RecoveryPlan</span> contract. Replacing the fixture with a
            real optimizer is a one-class change behind that interface.
          </p>
        </Disclosure>
      </div>
    </Panel>
  );
}

/* --------------------------------------------------------------------- kpi row */

/**
 * The headline numbers.
 *
 * `impact` lets the caller hand in the horizon the viewer has actually
 * selected. Without it the headline always reported the 10-day primary impact,
 * so choosing the 30-day horizon left a banner reading "10-day horizon"
 * directly above a panel reading "30 days". The number was correct; the page
 * contradicted itself, which is worse than either.
 */
export function HeroKpiRow({
  state,
  impact: selectedImpact,
}: {
  state: DemoState;
  impact?: ImpactPayload | null;
}): JSX.Element {
  const impact = selectedImpact ?? state.m1?.primary_impact;
  const plan = state.plan;
  const learning = state.learning;

  const revRow = learning?.scorecard.rows.find((r) => r.metric === "revenue_at_risk_usd");

  return (
    <div className="grid grid--4">
      <Kpi
        label="Revenue at risk"
        value={impact ? money(impact.revenue_at_risk, { compact: true }) : "—"}
        foot={
          impact
            ? `${moneyExact(impact.revenue_at_risk)} · ${impact.horizon_days}-day horizon`
            : "run the scenario"
        }
        accent="var(--danger)"
      />
      <Kpi
        label="Exposure band"
        value={impact ? percent(impact.stockout_probability, 1) : "—"}
        foot={impact ? "value-weighted stockout probability" : "no impact computed"}
        accent="var(--warn)"
      />
      <Kpi
        label="Recovery plan"
        value={<span style={{ fontSize: "var(--fs-lg)" }}>{plan?.strategy ?? "—"}</span>}
        foot={plan ? `${money(plan.cost, { compact: true })} · service ${percent(plan.service_level, 1)}` : "awaiting M2"}
        accent="var(--accent)"
      />
      <Kpi
        label="Outcome delta"
        value={
          revRow
            ? signedPercent(revRow.percent_delta)
            : state.governance?.state === "APPROVAL_REQUIRED"
              ? "pending"
              : "—"
        }
        foot={
          revRow
            ? `revenue exposure ${revRow.direction} than predicted`
            : state.governance?.state === "APPROVAL_REQUIRED"
              ? "awaiting human approval"
              : "no reconciliation yet"
        }
        accent={revRow?.direction === "better" ? "var(--ok)" : "var(--warn)"}
      />
    </div>
  );
}

export function ProgressBar({ value, label }: { value: number; label: string }): JSX.Element {
  return (
    <div className="stack stack--tight" style={{ gap: 4 }}>
      <span className="tiny muted">{label}</span>
      <Bar value={value} label={label} />
    </div>
  );
}
