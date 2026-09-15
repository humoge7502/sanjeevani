/**
 * M3 governance surfaces.
 *
 * The approval console is the most important component in the product: it is the
 * single moment the whole thesis rests on. It must make the decision
 * understandable in seconds and make the authority boundary unmistakable —
 * including the fact that the boundary is enforced by the server, not by
 * disabling a button here.
 */

import type {
  ApprovalRequest,
  ComplianceRecord,
  DemoState,
  ExecutionPayload,
  PolicyCheck,
  RecoveryPlanPayload,
} from "../lib/types";
import {
  OUTCOME_GLYPH,
  OUTCOME_TONE,
  TIER_TONE,
  clockTime,
  money,
  moneyExact,
  num,
  percent,
  riskLevel,
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

/* ------------------------------------------------------------ plan (M2) */

export function PlanPanel({
  state,
}: {
  state: DemoState;
}): JSX.Element {
  const plan = state.plan;
  if (!plan) {
    return (
      <Panel eyebrow="M2 boundary" title="Recovery plan">
        <EmptyState
          title="No plan received"
          hint="M3 consumes a RecoveryPlan from M2. Nothing is invented when it is absent."
          glyph="◇"
        />
      </Panel>
    );
  }
  const ranked = plan.alternatives.length > 0 ? plan.alternatives : [];

  return (
    <Panel
      eyebrow="M2 boundary · not implemented here"
      title="Recovery plan"
      subtitle={plan.rationale ?? undefined}
      actions={<Provenance kind="fixture" />}
    >
      <div className="stack">
        <Note tone="note--warn" glyph="⚠">
          <strong>{state.plan_provider.reality}</strong> — {state.plan_provider.disclosure}
        </Note>

        <div className="grid grid--4">
          <Kpi label="Recovery cost" value={money(plan.cost, { compact: true })} foot={moneyExact(plan.cost)} accent="var(--warn)" />
          <Kpi label="Service level" value={percent(plan.service_level, 1)} foot="post-recovery" accent="var(--ok)" />
          <Kpi label="Resilience score" value={num(plan.resilience_score, 3)} foot="0 = fragile, 1 = resilient" accent="var(--accent)" />
          <Kpi
            label="Temperature risk"
            value={num(plan.temperature_risk, 3)}
            foot="ceiling 0.35 for 2–8 °C"
            accent={plan.temperature_risk > 0.35 ? "var(--danger)" : "var(--ok)"}
          />
        </div>

        <div className="grid grid--2">
          <div className="stack stack--tight">
            <div className="small muted">Plan identity</div>
            <StatRow label="Plan ID" value={plan.plan_id} />
            <StatRow label="Strategy" value={plan.strategy} />
            <StatRow label="Recovery time" value={`${num(plan.recovery_time_hours, 1)} h`} />
            <StatRow label="Compliance status" value={plan.compliance_status} />
            <StatRow label="Provider source" value={plan.source} />
            <StatRow label="Contract version" value={plan.contract_version} />
          </div>
          <div className="stack stack--tight">
            <div className="small muted">Evidence carried by the plan</div>
            <div className="pill-row">
              <span className="small muted">SKUs:</span>
              {plan.impacted_skus.map((sku) => (
                <Badge key={sku} tone="badge--accent">
                  {sku}
                </Badge>
              ))}
            </div>
            <div className="pill-row">
              <span className="small muted">Lanes:</span>
              {plan.impacted_lanes.map((lane) => (
                <Badge key={lane} tone="badge--accent">
                  {lane}
                </Badge>
              ))}
            </div>
            <p className="tiny muted">
              A plan that cannot name what it affects cannot be audited, so
              <span className="mono"> POL-EVIDENCE-001 </span>
              fails it.
            </p>
          </div>
        </div>

        {ranked.length > 0 && <StrategyTable plan={plan} />}

        <Evidence title="RecoveryPlan contract payload" data={plan} />
      </div>
    </Panel>
  );
}

export function StrategyTable({ plan }: { plan: RecoveryPlanPayload }): JSX.Element {
  const best = {
    cost: Math.max(...plan.alternatives.map((a) => a.cost), 1),
  };
  return (
    <div className="panel panel--flush">
      <div className="panel__head">
        <div className="panel__titles">
          <p className="panel__eyebrow">M2 fixture strategy comparison</p>
          <h4 className="panel__title" style={{ fontSize: "var(--fs-md)" }}>
            Ranked alternatives
          </h4>
        </div>
      </div>
      <TableScroll label="Ranked recovery strategies supplied by the M2 boundary fixture">
        <table className="table">
          <caption className="visually-hidden">
            Ranked recovery strategies supplied by the M2 boundary fixture
          </caption>
          <thead>
            <tr>
              <th scope="col">Strategy</th>
              <th scope="col" className="num">
                Cost
              </th>
              <th scope="col">Cost scale</th>
              <th scope="col" className="num">
                Service
              </th>
              <th scope="col" className="num">
                Resilience
              </th>
              <th scope="col" className="num">
                Temp risk
              </th>
              <th scope="col" className="num">
                Recovery
              </th>
            </tr>
          </thead>
          <tbody>
            {plan.alternatives.map((alt) => (
              <tr key={alt.plan_id}>
                <td>
                  <div className="mono">{alt.strategy}</div>
                  <div className="row" style={{ marginTop: 3 }}>
                    {alt.recommended ? (
                      <Badge tone="badge--ok" glyph="✓">
                        Selected
                      </Badge>
                    ) : (
                      <Badge tone="badge--plain">alternative</Badge>
                    )}
                    <span className="tiny muted mono">{alt.plan_id}</span>
                  </div>
                </td>
                <td className="num">{money(alt.cost, { compact: true })}</td>
                <td style={{ minWidth: 90 }}>
                  <Bar
                    value={alt.cost / best.cost}
                    color={alt.recommended ? "var(--ok)" : "var(--text-faint)"}
                    label={`Cost for ${alt.strategy}`}
                  />
                </td>
                <td className="num">{percent(alt.service_level, 1)}</td>
                <td className="num">{num(alt.resilience_score, 3)}</td>
                <td className="num">
                  <span style={{ color: alt.temperature_risk > 0.35 ? "var(--danger)" : "var(--text)" }}>
                    {num(alt.temperature_risk, 3)}
                  </span>
                </td>
                <td className="num">{num(alt.recovery_time_hours, 1)} h</td>
              </tr>
            ))}
          </tbody>
        </table>
      </TableScroll>
      <div className="panel__body">
        <Note glyph="◈">
          The comparison table is part of the M2 fixture too. No simulation was
          replayed and no objective function was optimized to produce these rows —
          they are hand-set, deterministic and labelled as such.
        </Note>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------ compliance */

export function CompliancePanel({ record }: { record: ComplianceRecord }): JSX.Element {
  const checks = record.checks;
  const all: Array<{ check: PolicyCheck; outcome: string }> = [
    ...checks.failed.map((c) => ({ check: c, outcome: "FAIL" })),
    ...checks.errored.map((c) => ({ check: c, outcome: "ERROR" })),
    ...checks.warned.map((c) => ({ check: c, outcome: "WARN" })),
    ...checks.passed.map((c) => ({ check: c, outcome: "PASS" })),
  ];

  return (
    <Panel
      eyebrow="A5 Policy engine"
      title="Compliance evaluation"
      subtitle={record.rationale}
      actions={
        <div className="row">
          <Badge tone={TIER_TONE[record.risk_tier]} glyph="▲">
            Risk tier {record.risk_tier}
          </Badge>
          <StatusBadge
            status={record.compliance_status === "PASSED" ? "PASS" : "FAIL"}
            label={record.compliance_status}
          />
        </div>
      }
    >
      <div className="stack">
        <div className="grid grid--4">
          <Kpi label="Checks passed" value={checks.passed.length} foot={`of ${all.length} evaluated`} accent="var(--ok)" />
          <Kpi label="Checks failed" value={checks.failed.length} foot={checks.failed.length ? "blocks execution" : "none"} accent={checks.failed.length ? "var(--danger)" : "var(--ok)"} />
          <Kpi label="Warnings" value={checks.warned.length} foot="review required, not blocking" accent="var(--warn)" />
          <Kpi
            label="Required approver"
            value={<span style={{ fontSize: "var(--fs-lg)" }}>{record.required_role ?? "none"}</span>}
            foot={record.approval_required ? `tier ${record.tier}` : "below approval threshold"}
            accent="var(--violet)"
          />
        </div>

        <div className="grid grid--2">
          <div className="stack stack--tight">
            <div className="small muted">Scope the rulebook was evaluated against</div>
            <StatRow label="Temperature sensitive" value={record.scope.temperature_sensitive ? "yes" : "no"} />
            <StatRow label="Critical SKUs in scope" value={record.scope.critical_scope ? "yes" : "no"} />
            <StatRow label="SKUs" value={record.scope.sku_ids.join(", ") || "none"} />
            <StatRow label="Excursed consignments" value={record.scope.excursed_shipments.join(", ") || "none"} />
            <StatRow label="Rulebook" value={record.rulebook_version} />
          </div>
          <div className="stack stack--tight">
            <div className="small muted">Provenance labels used in this evaluation</div>
            <p className="small secondary">
              Every rule states what it is. Nothing here claims regulatory
              certification, and the compliance panel says so in its own words.
            </p>
            <Note tone="note--warn" glyph="⚠">
              {record.disclaimer}
              <div className="tiny" style={{ marginTop: 4 }}>
                Review status: <span className="mono">{record.review_status}</span>
              </div>
            </Note>
          </div>
        </div>

        <div className="panel panel--flush">
          <div className="panel__head">
            <div className="panel__titles">
              <p className="panel__eyebrow">Rulebook results</p>
              <h4 className="panel__title" style={{ fontSize: "var(--fs-md)" }}>
                {all.length} checks
              </h4>
            </div>
          </div>
          <div className="panel__body">
            {all.map(({ check, outcome }) => (
              <CheckRow key={check.rule_id} check={check} outcome={outcome} />
            ))}
          </div>
        </div>
      </div>
    </Panel>
  );
}

export function CheckRow({ check, outcome }: { check: PolicyCheck; outcome: string }): JSX.Element {
  const className = `check check--${outcome.toLowerCase()}`;
  const glyph = OUTCOME_GLYPH[outcome] ?? "•";
  return (
    <div className={className}>
      <span className="check__mark" aria-hidden="true">
        {glyph}
      </span>
      <div>
        <div className="row row--between">
          <span>
            <span className="check__id">{check.rule_id}</span>{" "}
            <span className="check__name">{check.name}</span>
          </span>
          <span className="pill-row">
            <span className={`prov prov--${check.provenance}`} title={check.provenance_label}>
              {check.provenance.replace(/_/g, " ")}
            </span>
            <Badge tone={OUTCOME_TONE[outcome] ?? ""} glyph={glyph}>
              {outcome}
            </Badge>
          </span>
        </div>
        <div className="check__msg">{check.message}</div>
        <details className="disclosure" style={{ marginTop: 6 }}>
          <summary>Threshold & actual</summary>
          <div className="evidence__body">
            <DefList
              rows={[
                ["Dimension", <span className="mono">{check.dimension}</span>],
                ["Severity if triggered", <span className="mono">{check.severity}</span>],
                ["Threshold", <span className="mono break">{JSON.stringify(check.threshold)}</span>],
                ["Actual", <span className="mono break">{JSON.stringify(check.actual)}</span>],
                ["Config key", <span className="mono break">{String(check.evidence["config_key"] ?? "—")}</span>],
                ["Provenance", check.provenance_label],
              ]}
            />
          </div>
        </details>
      </div>
    </div>
  );
}

/* ------------------------------------------------------- approval console */

export function ApprovalConsole({
  request,
  state,
  actorId,
  rationale,
  busy,
  onActorChange,
  onRationaleChange,
  onDecide,
}: {
  request: ApprovalRequest;
  state: DemoState;
  actorId: string;
  rationale: string;
  busy: boolean;
  onActorChange: (value: string) => void;
  onRationaleChange: (value: string) => void;
  onDecide: (decision: "approve" | "reject" | "request-review") => void;
}): JSX.Element {
  const govState = state.governance?.state ?? "UNKNOWN";
  const approved = govState === "APPROVED" || govState === "COMPLETED" || govState === "EXECUTED";
  const rejected = govState === "REJECTED";
  const canAct = govState === "APPROVAL_REQUIRED";

  const actor = state.approvers.find((a) => a.id === actorId);
  const actorRole = actor?.role ?? null;
  const authorized = request.required_role === null || actorRole === request.required_role;

  const containerClass = approved
    ? "approval approval--approved"
    : rejected
      ? "approval approval--rejected"
      : "approval";

  const level = riskLevel(request.impact.stockout_probability);

  return (
    <section className={containerClass} aria-labelledby="approval-heading">
      <div className="approval__banner">
        <div style={{ flex: 1, minWidth: 0 }}>
          <p className="panel__eyebrow">
            Human-in-the-loop gate · consequence tier {request.risk_tier}
          </p>
          <h3 className="approval__headline" id="approval-heading">
            {approved
              ? "Approved — governed execution authorized"
              : rejected
                ? "Rejected — execution blocked"
                : canAct
                  ? "One human decision required"
                  : "Awaiting compliance"}
          </h3>
          <p className="approval__gate">
            Required authority: <span className="mono">{request.required_role ?? "none"}</span> ·
            current state <span className="mono">{govState}</span>
          </p>
        </div>
        <div className="row">
          <Badge tone={TIER_TONE[request.risk_tier]} glyph="▲">
            {request.risk_tier}
          </Badge>
          <StatusBadge status={govState} />
        </div>
      </div>

      <div className="panel__body">
        <div className="stack">
          <div className="grid grid--4">
            <Kpi label="Recommended action" value={<span style={{ fontSize: "var(--fs-lg)" }}>{request.strategy}</span>} foot={`plan ${request.plan_id}`} accent="var(--accent)" />
            <Kpi label="Cost" value={money(request.cost, { compact: true })} foot={moneyExact(request.cost)} accent="var(--warn)" />
            <Kpi label="Revenue at risk if deferred" value={money(request.impact.revenue_at_risk, { compact: true })} foot={`${request.impact.horizon_days}-day horizon`} accent="var(--danger)" />
            <Kpi label="Expected service level" value={percent(request.service_level, 1)} foot={`resilience ${num(request.resilience_score, 3)}`} accent="var(--ok)" />
          </div>

          <div className="grid grid--2">
            <div className="stack stack--tight">
              <div className="small muted">Why this plan</div>
              <p className="small secondary" style={{ margin: 0 }}>
                {request.rationale ?? "No rationale supplied by the plan source."}
              </p>
              <div className="divider" />
              <div className="small muted">Decision inputs</div>
              <StatRow label="Plan source" value={request.plan_source} />
              <StatRow label="Temperature risk" value={num(request.temperature_risk, 3)} />
              <StatRow label="Recovery time" value={`${num(request.recovery_time_hours, 1)} h`} />
              <StatRow label="Stockout exposure" value={percent(request.impact.stockout_probability, 1)} />
              <StatRow label="Service-level risk" value={percent(request.impact.service_level_risk, 1)} />
              <StatRow label="Affected consignments" value={request.impact.affected_shipments.join(", ")} />
              <StatRow label="Affected products" value={request.impact.affected_products.join(", ")} />
              <StatRow label="Exposure band" value={<Badge tone={level.tone} glyph="▲">{level.label}</Badge>} mono={false} />
            </div>

            <div className="stack stack--tight">
              <div className="small muted">Compliance result</div>
              <StatRow label="Status" value={request.compliance.status} />
              <StatRow
                label="Checks"
                value={`${request.compliance.summary.passed} passed · ${request.compliance.summary.failed} failed · ${request.compliance.summary.warned} warned`}
              />
              {request.compliance.warnings.length > 0 && (
                <Note tone="note--warn" glyph="!">
                  {request.compliance.warnings.map((w, i) => (
                    <div key={i} className="small">
                      {w}
                    </div>
                  ))}
                </Note>
              )}
              {request.compliance.failed.length > 0 && (
                <Note tone="note--danger" glyph="✕">
                  {request.compliance.failed.map((f, i) => (
                    <div key={i} className="small">
                      {f}
                    </div>
                  ))}
                </Note>
              )}
              <div className="divider" />
              <div className="small muted">Expected execution summary</div>
              <div className="stack stack--tight">
                {request.impact.affected_nodes.length > 0 && (
                  <div className="tiny muted">
                    Nodes in scope: <span className="mono">{request.impact.affected_nodes.join(", ")}</span>
                  </div>
                )}
              </div>
            </div>
          </div>

          <div>
            <div className="small muted" style={{ marginBottom: "var(--sp-2)" }}>
              What will happen after approval
            </div>
            <div className="exec-preview">
              {request.what_will_happen.map((step) => (
                <div className="exec-preview__item" key={step.system}>
                  <div className="row row--between">
                    <span className="exec-preview__sys">{step.system}</span>
                    <Provenance kind="mocked" text="mock" />
                  </div>
                  <div className="small secondary" style={{ marginTop: 4 }}>
                    {step.action}
                  </div>
                  <div className="tiny muted" style={{ marginTop: 4 }}>
                    {step.label}
                  </div>
                </div>
              ))}
            </div>
          </div>

          <Note tone="note--info" glyph="🔒">
            {request.withhold}
          </Note>
        </div>
      </div>

      <div className="approval__actions">
        <div className="stack stack--tight" style={{ flex: 1, minWidth: 240 }}>
          <label className="small muted" htmlFor="approver">
            Acting as
          </label>
          <select
            id="approver"
            className="btn"
            style={{ justifyContent: "flex-start", width: "100%" }}
            value={actorId}
            onChange={(event) => onActorChange(event.target.value)}
            disabled={!canAct || busy}
          >
            {state.approvers.map((a) => (
              <option key={a.id} value={a.id}>
                {a.full_name} — {a.title} ({a.role})
              </option>
            ))}
            <option value="unknown.actor">Unknown actor (no role)</option>
          </select>
          <label className="small muted" htmlFor="rationale">
            Rationale (recorded in the ledger)
          </label>
          <input
            id="rationale"
            className="btn"
            style={{ width: "100%", justifyContent: "flex-start", fontWeight: 400 }}
            type="text"
            value={rationale}
            placeholder="e.g. Protect the biologic supply; cost is inside the L3 cap."
            onChange={(event) => onRationaleChange(event.target.value)}
            disabled={!canAct || busy}
          />
          {!authorized && canAct && (
            <Note tone="note--danger" glyph="✕">
              {actor?.full_name ?? "This actor"} holds role{" "}
              <span className="mono">{actorRole ?? "none"}</span>, but this plan requires{" "}
              <span className="mono">{request.required_role}</span>. The backend will refuse.
            </Note>
          )}
        </div>

        <div className="stack stack--tight">
          <button
            type="button"
            className="btn btn--approve btn--lg"
            disabled={!canAct || busy || !authorized}
            onClick={() => onDecide("approve")}
          >
            <span className="btn__glyph" aria-hidden="true">
              ✓
            </span>
            Approve &amp; authorize
          </button>
          <button
            type="button"
            className="btn btn--danger"
            disabled={!canAct || busy}
            onClick={() => onDecide("reject")}
          >
            <span className="btn__glyph" aria-hidden="true">
              ✕
            </span>
            Reject
          </button>
          <button
            type="button"
            className="btn btn--ghost"
            disabled={!canAct || busy}
            onClick={() => onDecide("request-review")}
          >
            <span className="btn__glyph" aria-hidden="true">
              !
            </span>
            Request review
          </button>
          <p className="tiny muted" style={{ maxWidth: 260 }}>
            Buttons reflect server state only. Even a forged request cannot execute
            without an APPROVED state from the state machine.
          </p>
        </div>
      </div>
    </section>
  );
}

/* ------------------------------------------------------------- execution */

export function ExecutionPanel({ execution }: { execution: ExecutionPayload }): JSX.Element {
  const receipt = execution.receipt;
  const statusFor = (system: string): string => {
    if (system === "ibp") return receipt.ibp;
    if (system === "tm") return receipt.tm;
    return receipt.ariba;
  };

  return (
    <Panel
      eyebrow="A5 Execution · SAP-shaped mocks"
      title="Governed execution & receipts"
      subtitle="One human approval, three transactional posts, one correlated receipt."
      actions={
        <div className="row">
          <Provenance kind="mocked" text="mock boundary" />
          {execution.replayed && <Badge tone="badge--info" glyph="↺">replayed</Badge>}
        </div>
      }
    >
      <div className="stack">
        <div className="grid grid--4">
          <Kpi label="IBP" value={<StatusBadge status={statusFor("ibp")} />} foot="planning scenario" accent="var(--accent)" />
          <Kpi label="TM" value={<StatusBadge status={statusFor("tm")} />} foot="freight re-booking" accent="var(--info)" />
          <Kpi label="Ariba" value={<StatusBadge status={statusFor("ariba")} />} foot="supplier risk" accent="var(--violet)" />
          <Kpi
            label="Receipt"
            value={<StatusBadge status={execution.failure ? "FAILED" : "SUCCESS"} />}
            foot={`${execution.actions.length} actions· ${clockTime(receipt.timestamp)}`}
            accent={execution.failure ? "var(--danger)" : "var(--ok)"}
          />
        </div>

        <div className="grid grid--2">
          <div className="stack stack--tight">
            <div className="small muted">Correlation</div>
            <StatRow label="Execution ID" value={receipt.execution_id ?? "—"} />
            <StatRow label="Correlation ID" value={receipt.correlation_id ?? "—"} />
            <StatRow label="Plan" value={receipt.plan_id} />
            <StatRow label="Timestamp" value={receipt.timestamp} />
            <StatRow label="Mock" value={receipt.mock ? "yes — no SAP tenant contacted" : "no"} />
          </div>
          <div className="stack stack--tight">
            <div className="small muted">Defined compensating actions</div>
            {execution.compensating_actions.map((c, i) => (
              <div className="note" key={`${c.system}-${i}`}>
                <span className="note__glyph" aria-hidden="true">
                  ↩
                </span>
                <div>
                  <div className="small">
                    <span className="mono">{c.system}</span> · {c.action}
                  </div>
                  <div className="tiny muted">{c.description}</div>
                </div>
              </div>
            ))}
          </div>
        </div>

        {execution.failure && (
          <Note tone="note--danger" glyph="✕">
            <strong>{execution.failure.system}</strong> failed: {execution.failure.message} —
            remaining steps were skipped rather than retried, and the receipt reports the partial
            result honestly.
          </Note>
        )}

        <div className="panel panel--flush">
          <div className="panel__head">
            <div className="panel__titles">
              <p className="panel__eyebrow">Transactional sequence</p>
              <h4 className="panel__title" style={{ fontSize: "var(--fs-md)" }}>
                IBP → TM → Ariba
              </h4>
            </div>
          </div>
          <div className="panel__body">
            {execution.actions.map((action, index) => {
              const status = action.status;
              const tone =
                status === "SUCCESS" ? "receipt-step--ok" : status === "FAILED" ? "receipt-step--fail" : "receipt-step--skipped";
              return (
                <div className={`receipt-step ${tone}`} key={`${action.system}-${index}`}>
                  <span className="receipt-step__num" aria-hidden="true">
                    {index + 1}
                  </span>
                  <div>
                    <div className="row row--between">
                      <span>
                        <span className="mono">{action.label ?? action.system}</span>{" "}
                        <span className="tiny muted">{action.operation ?? ""}</span>
                      </span>
                      <span className="pill-row">
                        <Provenance kind="mocked" text="mock" />
                        <StatusBadge status={status} />
                      </span>
                    </div>
                    <div className="small secondary" style={{ marginTop: 2 }}>
                      {action.summary ?? action.reason ?? action.error?.message ?? "—"}
                    </div>
                    {action.response && (
                      <details className="disclosure" style={{ marginTop: 6 }}>
                        <summary>OData-shaped response</summary>
                        <div className="evidence__body">
                          <pre className="json">{JSON.stringify(action.response, null, 2)}</pre>
                        </div>
                      </details>
                    )}
                  </div>
                  <span className="tiny muted mono">
                    {execution.replayed ? "replayed" : "posted"}
                  </span>
                </div>
              );
            })}
          </div>
        </div>

        <Disclosure title="Why the sequencing is IBP → TM → Ariba">
          <ul className="small secondary" style={{ margin: 0, paddingLeft: 18 }}>
            <li>
              <strong>IBP first.</strong> The planning scenario is the key-figure record of the
              decision; freight changes made against a non-existent scenario would be orphaned.
            </li>
            <li>
              <strong>TM second.</strong> Freight is re-booked against the scenario that now exists.
            </li>
            <li>
              <strong>Ariba last.</strong> Sourcing risk is a reviewable failure, not a physical
              one, so it is the safest step to fail last.
            </li>
            <li>
              <strong>No silent retry.</strong> A failure stops the sequence, records{" "}
              <span className="mono">SKIPPED</span> for the remainder and issues an honest receipt.
            </li>
          </ul>
        </Disclosure>

        <Evidence title="ExecutionReceipt contract payload" data={receipt} />
      </div>
    </Panel>
  );
}
