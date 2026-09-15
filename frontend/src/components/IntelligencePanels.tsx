/**
 * M1 intelligence surfaces: what happened, how trustworthy it is, what scenario
 * it creates, and what is affected.
 *
 * Every derived number is rendered with a route to its origin: the consignment
 * table carries the per-shipment arithmetic, and each panel exposes a raw
 * evidence drawer. That is the product claim ("every number traceable") made
 * visible rather than asserted.
 */

import type {
  ConsignmentRow,
  ExcursionAnalysis,
  ImpactPayload,
  M1Snapshot,
  VerifiedEvent,
} from "../lib/types";
import {
  SEVERITY_GLYPH,
  SEVERITY_TONE,
  money,
  moneyExact,
  num,
  percent,
  riskLevel,
  clockTime,
} from "../lib/format";
import {
  Badge,
  Bar,
  DefList,
  Disclosure,
  EmptyState,
  Evidence,
  Kpi,
  MeterRow,
  Note,
  Panel,
  Provenance,
  StatRow,
  TableScroll,
} from "./primitives";

/* --------------------------------------------------------------- signals */

export function SignalPanel({
  m1,
  primaryEvent,
}: {
  m1: M1Snapshot;
  primaryEvent: VerifiedEvent | undefined;
}): JSX.Element {
  if (!primaryEvent) {
    return (
      <Panel eyebrow="A1 · A2" title="Incident / signal">
        <EmptyState
          title="No verified event"
          hint="Nothing reached the verification gate, so there is nothing to act on."
        />
      </Panel>
    );
  }

  const v = primaryEvent.verification;
  const trace = v.trace;

  return (
    <Panel
      eyebrow="A1 Sensing · A2 Verification"
      title="Incident & signal"
      subtitle={`${m1.events.length} verified event(s) from ${m1.events.length + m1.watchlist.length + m1.rejected.length} classified candidates`}
      actions={
        <Badge tone={SEVERITY_TONE[primaryEvent.severity]} glyph={SEVERITY_GLYPH[primaryEvent.severity]}>
          {primaryEvent.severity}
        </Badge>
      }
    >
      <div className="stack">
        <DefList
          rows={[
            ["Event ID", <span className="mono">{primaryEvent.event_id}</span>],
            ["Type", <span className="mono">{primaryEvent.type}</span>],
            ["Target", <span className="mono">{primaryEvent.target}</span>],
            [
              "Confidence",
              <span className="mono">
                {num(primaryEvent.confidence, 4)}{" "}
                <span className="muted tiny">(must be ≥ 0.60 to escalate)</span>
              </span>,
            ],
            ["Primary source class", <span className="mono">{primaryEvent.source}</span>],
            ["Verified at", <span className="mono">{clockTime(primaryEvent.timestamp)}</span>],
            ["Contributing signals", <span className="mono">{primaryEvent.contributing_signals.join(", ")}</span>],
            ["Verification rule", <span className="mono">{v.rule}</span>],
            ["Freshness", <span className="mono">{v.freshness ?? "—"}</span>],
            [
              "Signal age",
              <span className="mono">{v.signal_age_hours !== undefined ? `${num(v.signal_age_hours, 2)} h` : "—"}</span>,
            ],
          ]}
        />

        <Note tone="note--info" glyph="ℹ">
          <strong>{v.rule}</strong> — {v.reason}
          {v.confidence_math && (
            <>
              {" "}
              <span className="mono small">Confidence: {v.confidence_math}</span>
            </>
          )}
        </Note>

        {v.provenance && (
          <DefList
            rows={[
              [
                "Provenance",
                <a
                  className="mono small"
                  href={v.provenance.url.startsWith("http") ? v.provenance.url : undefined}
                  title={v.provenance.url}
                >
                  {v.provenance.url}
                </a>,
              ],
              ["Published", <span className="mono">{v.provenance.published_utc}</span>],
              ["Retrieved", <span className="mono">{v.provenance.retrieved_utc}</span>],
            ]}
          />
        )}

        {v.source_classes && (
          <div className="pill-row">
            <span className="small muted">Source classes in the cluster:</span>
            {v.source_classes.map((sc) => (
              <Badge key={sc} tone={v.fresh_source_classes?.includes(sc) ? "badge--ok" : "badge--plain"}>
                {sc}
                {v.fresh_source_classes?.includes(sc) ? " ✓" : " (stale)"}
              </Badge>
            ))}
          </div>
        )}

        {trace && <ExcursionCard analysis={trace} />}

        <Disclosure title="Free text is untrusted — the claim is what parameterizes the model">
          <p className="small secondary">
            A1 keeps the headline and body for the audit trail only. Agent parameters
            come from the structured <span className="mono">claim</span> payload, so
            text in a feed cannot reach a tool call even if it tries.
          </p>
          <pre className="json">{JSON.stringify(primaryEvent.verification, null, 2)}</pre>
        </Disclosure>
      </div>
    </Panel>
  );
}

export function ExcursionCard({ analysis }: { analysis: ExcursionAnalysis }): JSX.Element {
  return (
    <div className="panel panel--flush">
      <div className="panel__head">
        <div className="panel__titles">
          <p className="panel__eyebrow">IoT device trace · {analysis.device_id}</p>
          <h4 className="panel__title" style={{ fontSize: "var(--fs-md)" }}>
            Cold-chain excursion on {analysis.shipment_id}
          </h4>
        </div>
        <Badge tone={analysis.breached ? "badge--danger" : "badge--ok"} glyph={analysis.breached ? "✕" : "✓"}>
          {analysis.breached ? "Breached" : "Within tolerance"}
        </Badge>
      </div>
      <div className="panel__body">
        <div className="stack stack--tight">
          <div className="row">
            <Badge tone="badge--plain">{analysis.sample_count} samples</Badge>
            <Provenance kind={analysis.method.labelled_as} />
          </div>
          <DefList
            rows={[
              ["Limit", <span className="mono">≤ {num(analysis.limit_max_c, 1)} °C</span>],
              ["Peak", <span className="mono" style={{ color: "var(--danger)" }}>{num(analysis.peak_temp_c, 2)} °C</span>],
              ["Peak excess", <span className="mono">+{num(analysis.peak_excess_c, 2)} °C</span>],
              ["Minutes above limit", <span className="mono">{num(analysis.minutes_above_limit, 1)} min</span>],
              ["Cumulative load", <span className="mono">{num(analysis.degree_minutes / 60, 3)} °C·h</span>],
              ["Condemned fraction", <span className="mono">{percent(analysis.condemn_ratio, 2)}</span>],
            ]}
          />
          <Note tone="note--warn" glyph="!">
            <strong>Condemnation is an illustrative model, not a regulatory rule.</strong>{" "}
            Band: <span className="mono">{analysis.method.band}</span> · {analysis.method.ratio_formula}
          </Note>
          <Disclosure title="Condemnation model thresholds">
            <pre className="json">{JSON.stringify(analysis.method, null, 2)}</pre>
          </Disclosure>
        </div>
      </div>
    </div>
  );
}

export function WatchlistPanel({ m1 }: { m1: M1Snapshot }): JSX.Element {
  const hasRows = m1.watchlist.length + m1.rejected.length + m1.deduplicated.length > 0;
  return (
    <Panel
      eyebrow="A2 gate"
      title="Held back, rejected & deduplicated"
      subtitle="Credibility control: what the system deliberately did NOT act on."
    >
      {!hasRows && <EmptyState title="Nothing withheld" glyph="✓" />}
      {hasRows && (
        <div className="stack stack--tight">
          {m1.rejected.map((r, i) => (
            <Note key={`rej-${i}`} tone="note--danger" glyph="✕">
              <strong>{r.event_type}</strong> on <span className="mono">{r.target}</span> was{" "}
              <strong>not verified</strong>: {String(r.verification["reason"] ?? "")}{" "}
              <span className="muted">No event was created, so it cannot affect the impact.</span>
            </Note>
          ))}
          {m1.deduplicated.map((d) => (
            <Note key={d.signal_id} glyph="≡">
              <span className="mono">{d.signal_id}</span> ({d.source_class}) deduplicated into{" "}
              <span className="mono">{d.duplicate_of}</span> — {d.note}
            </Note>
          ))}
          {m1.watchlist.map((w) => (
            <Note key={w.candidate_id} glyph="○">
              <div className="row row--between">
                <span>
                  <strong>{w.event_type}</strong> on <span className="mono">{w.target}</span>
                </span>
                <span className="pill-row">
                  <Badge tone="badge--plain">{w.freshness}</Badge>
                  <Badge tone="badge--plain">conf {num(w.raw_confidence, 3)}</Badge>
                  <Badge tone="badge--plain">{w.source_class}</Badge>
                </span>
              </div>
              <div className="small secondary" style={{ marginTop: 2 }}>
                {w.headline}
              </div>
              <div className="tiny muted">{w.reasons[w.reasons.length - 1]}</div>
            </Note>
          ))}
        </div>
      )}
    </Panel>
  );
}

/* ------------------------------------------------------------- scenarios */

export function ScenarioPanel({
  m1,
  selectedHorizon,
  onSelectHorizon,
}: {
  m1: M1Snapshot;
  selectedHorizon: number;
  onSelectHorizon: (days: number) => void;
}): JSX.Element {
  const selected = m1.scenarios.find((s) => s.horizon_days === selectedHorizon) ?? m1.scenarios[0];
  const impact = m1.impacts.find((i) => i.horizon_days === selectedHorizon) ?? m1.primary_impact;
  const totalConfidence = impact.trace.inputs["duration_days"];

  return (
    <Panel
      eyebrow="A2 Scenario"
      title="Parameterized futures"
      subtitle="Clamped disruption parameters evaluated over three configured horizons."
    >
      <div className="stack">
        <div className="scenario-tabs" role="group" aria-label="Scenario horizon">
          {m1.scenarios.map((s) => (
            <button
              key={s.scenario_id}
              type="button"
              className="scenario-tab"
              aria-pressed={s.horizon_days === selectedHorizon}
              onClick={() => onSelectHorizon(s.horizon_days)}
            >
              {s.horizon_days}-day
              <span className="tiny muted" style={{ display: "block" }}>
                weight {percent(s.probability_weight, 0)}
              </span>
            </button>
          ))}
        </div>

        {selected && (
          <>
            <Note glyph="◇">{selected.narrative}</Note>

            <div className="grid grid--2" style={{ gap: "var(--sp-3)" }}>
              <div className="stack stack--tight">
                <StatRow label="Horizon" value={`${selected.horizon_days} days`} />
                <StatRow label="Closure duration" value={`${num(selected.duration_days, 1)} days`} />
                <StatRow label="Added transit" value={`${num(selected.transit_delay_days, 1)} days`} />
                <StatRow label="Capacity factor" value={`${num(selected.capacity_factor, 2)}×`} />
                <StatRow label="Event confidence" value={num(selected.confidence, 4)} />
                <StatRow label="Probability weight" value={percent(selected.probability_weight, 2)} />
              </div>
              <div className="stack stack--tight">
                <div className="small muted">Horizon weighting</div>
                {m1.scenarios.map((s) => (
                  <MeterRow
                    key={s.scenario_id}
                    label={`${s.horizon_days}-day`}
                    valueText={percent(s.probability_weight, 1)}
                    value={s.probability_weight}
                    color={
                      s.horizon_days === selectedHorizon ? "var(--accent)" : "var(--series-3)"
                    }
                  />
                ))}
                <p className="tiny muted">
                  alignment(H) = exp(−|H − duration| / max(duration, 1)); the horizon matching
                  the disruption's own duration dominates.
                </p>
              </div>
            </div>

            <Disclosure title="Clamping & parameter provenance">
              <DefList
                rows={[
                  ["Claim origin", <span className="mono">{selected.parameters_source.origin}</span>],
                  ["Source event", <span className="mono">{selected.parameters_source.event_id}</span>],
                  ...Object.entries(selected.parameters_source.bounds).map(
                    ([key, bounds]): [string, JSX.Element] => [
                      `${key} bounds`,
                      <span className="mono">
                        [{bounds[0]} … {bounds[1]}]{" "}
                        {selected.parameters_source.clamped[key] ? (
                          <Badge tone="badge--warn" glyph="!">
                            clamped
                          </Badge>
                        ) : (
                          <Badge tone="badge--plain">within bounds</Badge>
                        )}
                      </span>,
                    ],
                  ),
                ]}
              />
              <p className="tiny muted" style={{ marginTop: "var(--sp-2)" }}>
                A2 may shrink a claim against configured bounds but can never inflate one.
                {totalConfidence !== undefined && ` Base claim: ${JSON.stringify(totalConfidence)}.`}
              </p>
            </Disclosure>
          </>
        )}
      </div>
    </Panel>
  );
}

/* ---------------------------------------------------------------- impact */

export function ImpactPanel({ impact }: { impact: ImpactPayload }): JSX.Element {
  const level = riskLevel(impact.stockout_probability);
  const graph = impact.trace.graph;
  const agg = impact.trace.aggregation;

  const slRiskByMarket = [...impact.trace.service_level_by_market].sort(
    (a, b) => b.breach_points - a.breach_points,
  );

  return (
    <div className="stack">
      <div className="grid grid--4">
        <Kpi
          label="Revenue at risk"
          value={money(impact.revenue_at_risk, { compact: true })}
          foot={moneyExact(impact.revenue_at_risk)}
          accent="var(--danger)"
          hero
        />
        <Kpi
          label="Stockout probability"
          value={percent(impact.stockout_probability, 1)}
          foot={`value-weighted · ${level.label} exposure`}
          accent={level.barColor}
          hero
        />
        <Kpi
          label="Service-level risk"
          value={percent(impact.service_level_risk, 1)}
          foot="expected breach in service-level points"
          accent="var(--warn)"
          hero
        />
        <Kpi
          label="Unaffected carry"
          value={moneyExact(agg.total_consignment_value_usd - impact.revenue_at_risk)}
          foot={`of ${money(agg.total_consignment_value_usd, { compact: true })} in scope`}
          accent="var(--ok)"
          hero
        />
      </div>

      <div className="grid grid--4">
        <Kpi label="Affected nodes" value={impact.affected_nodes.length} foot={`of 12 in the network`} accent="var(--warn)" />
        <Kpi
          label="Affected consignments"
          value={impact.affected_shipments.length}
          foot={impact.affected_shipments.join(", ")}
          accent="var(--warn)"
        />
        <Kpi
          label="Affected products"
          value={impact.affected_products.length}
          foot={impact.affected_products.join(", ")}
          accent="var(--warn)"
        />
        <Kpi
          label="Horizon"
          value={`${impact.horizon_days}`}
          unit=" days"
          foot={`weight ${percent(impact.probability_weight ?? 0, 1)} · ${impact.scenario_id}`}
          accent="var(--accent)"
        />
      </div>

      <Panel
        eyebrow="A3 Network Impact"
        title="Propagation and scope"
        subtitle={impact.trace.method}
        actions={<Provenance kind={impact.trace.labelled_as} />}
      >
        <div className="grid grid--2">
          <div className="stack stack--tight">
            <div className="small muted">Graph propagation</div>
            <StatRow label="Blocked nodes" value={graph.blocked_nodes.join(", ") || "none"} />
            <StatRow label="Disrupted lanes" value={graph.disrupted_lanes.join(", ") || "none"} />
            <StatRow
              label="Downstream of block"
              value={graph.downstream_of_blocked.join(", ") || "none"}
            />
            <StatRow
              label="Upstream of block"
              value={graph.upstream_of_blocked.join(", ") || "none"}
            />
            <StatRow label="Affected nodes" value={`${graph.affected_nodes.length}`} />
            <p className="tiny muted">{graph.formula}</p>
          </div>
          <div className="stack stack--tight">
            <div className="small muted">Service level by market</div>
            {slRiskByMarket.map((m) => (
              <div key={m.market} className="stack stack--tight" style={{ gap: 2 }}>
                <MeterRow
                  label={`${m.market} · target ${percent(m.service_target, 0)}`}
                  valueText={percent(m.achieved_service_level, 1)}
                  value={m.achieved_service_level}
                  color={m.breach_points > 0.05 ? "var(--danger)" : "var(--ok)"}
                />
                <span className="tiny muted mono">{m.formula}</span>
              </div>
            ))}
          </div>
        </div>
      </Panel>

      <ConsignmentTable rows={impact.trace.consignments} />

      <Evidence title="Impact contract payload (raw)" data={impact}>
        <p className="small secondary">
          This is the exact JSON M2 receives. Contract keys:{" "}
          <span className="mono">
            event_id, affected_nodes, affected_shipments, revenue_at_risk, stockout_probability,
            service_level_risk
          </span>
          . Everything else is additive and optional.
        </p>
      </Evidence>
    </div>
  );
}

export function ConsignmentTable({ rows }: { rows: ConsignmentRow[] }): JSX.Element {
  return (
    <Panel
      eyebrow="Traceability"
      title="Per-consignment arithmetic"
      subtitle="Each row shows the inputs and the formula that produced its number."
      flush
    >
      <TableScroll label="Per-consignment arithmetic table">
        <table className="table">
          <caption className="visually-hidden">
            Per-consignment stockout, delay, condemnation and value at risk
          </caption>
          <thead>
            <tr>
              <th scope="col">Consignment</th>
              <th scope="col">SKU</th>
              <th scope="col">Market</th>
              <th scope="col">At node</th>
              <th scope="col" className="num">
                Value
              </th>
              <th scope="col" className="num">
                Cover
              </th>
              <th scope="col" className="num">
                Delay
              </th>
              <th scope="col" className="num">
                Shortfall
              </th>
              <th scope="col" className="num">
                P(stockout)
              </th>
              <th scope="col" className="num">
                Condemned
              </th>
              <th scope="col" className="num">
                At risk
              </th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => {
              const level = riskLevel(row.p_stockout);
              return (
                <tr key={row.shipment_id}>
                  <td className="mono nowrap">
                    {row.shipment_id}
                    <div>
                      <Badge tone={level.tone} glyph="▲">
                        {level.label}
                      </Badge>
                    </div>
                  </td>
                  <td>
                    <div className="mono">{row.sku_id}</div>
                    <div className="tiny muted">{row.sku_name}</div>
                  </td>
                  <td className="mono">{row.market}</td>
                  <td className="mono tiny">{row.current_node}</td>
                  <td className="num">{moneyExact(row.value_usd)}</td>
                  <td className="num">{num(row.coverage_days, 1)}d</td>
                  <td className="num">{num(row.delay_days, 2)}d</td>
                  <td className="num">{num(row.shortfall_days, 2)}d</td>
                  <td className="num">
                    <span style={{ color: level.barColor }}>{percent(row.p_stockout, 2)}</span>
                    <div style={{ marginTop: 3, minWidth: 58 }}>
                      <Bar value={row.p_stockout} color={level.barColor} label={`P(stockout) for ${row.shipment_id}`} />
                    </div>
                  </td>
                  <td className="num">{percent(row.condemn_ratio, 2)}</td>
                  <td className="num">{moneyExact(row.value_at_risk_usd)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </TableScroll>
      <div className="panel__body" style={{ borderTop: "1px solid var(--border-subtle)" }}>
        <Disclosure title="Show the arithmetic for every row">
          <div className="stack stack--tight">
            {rows.map((row) => (
              <div key={row.shipment_id}>
                <div className="mono small">{row.shipment_id}</div>
                <ul className="small secondary" style={{ margin: "2px 0 0", paddingLeft: 18 }}>
                  {row.reasons.map((reason, i) => (
                    <li key={i}>{reason}</li>
                  ))}
                </ul>
              </div>
            ))}
          </div>
        </Disclosure>
      </div>
    </Panel>
  );
}
