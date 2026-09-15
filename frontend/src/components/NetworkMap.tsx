/**
 * Network impact map.
 *
 * Deliberately hand-drawn SVG rather than a graph library: the network is a
 * fixed 12-node seeded graph whose layout we control, so a physics/force layout
 * would add a dependency, jitter and non-determinism in exchange for nothing.
 * Positions are static, which means the map looks identical on every projection
 * surface and during every rehearsal.
 *
 * The map is informative, not decorative: every visual channel encodes something
 * present in the payload —
 *   node ring   -> affected by the disruption
 *   node fill   -> node type
 *   node cross  -> out of service (blocked)
 *   lane colour -> affected / red-sea-exposed / normal
 *   lane dash   -> the lane is severed by the block
 *   lane badge  -> how many consignments are on it
 *
 * Accessibility: the SVG is labelled, marked focusable nodes carry titles, and
 * an equivalent table is always rendered beneath it so no information is
 * available only visually.
 */

import type { NetworkSnapshot } from "../lib/types";
import { Provenance } from "./primitives";

const VIEW_W = 900;
const VIEW_H = 400;

const POSITIONS: Record<string, [number, number]> = {
  "SUP-CHN-A": [90, 60],
  "SUP-CHN-B": [58, 122],
  "SUP-IND-A": [168, 336],
  "SUP-EU-A": [706, 52],
  "PLANT-HYD": [232, 300],
  "PLANT-MUM": [302, 236],
  "PORT-JNPT": [228, 150],
  "PORT-SUEZ": [470, 118],
  "WH-BOM": [332, 176],
  "WH-DEL": [392, 292],
  "WH-FRA": [652, 130],
  "CUST-EU-HUB": [822, 186],
};

const TYPE_COLOR: Record<string, string> = {
  supplier: "var(--violet)",
  plant: "var(--accent)",
  port: "var(--info)",
  warehouse: "var(--ok)",
  customer: "var(--warn)",
};

const TYPE_RADIUS: Record<string, number> = {
  supplier: 11,
  plant: 14,
  port: 11,
  warehouse: 12,
  customer: 17,
};

const SHORT_LABEL: Record<string, string> = {
  "SUP-CHN-A": "SUP-CN-A",
  "SUP-CHN-B": "SUP-CN-B",
  "SUP-IND-A": "SUP-IN-A",
  "SUP-EU-A": "SUP-EU-A",
  "PLANT-HYD": "PLANT-HYD",
  "PLANT-MUM": "PLANT-MUM",
  "PORT-JNPT": "JNPT",
  "PORT-SUEZ": "SUEZ",
  "WH-BOM": "WH-BOM",
  "WH-DEL": "WH-DEL",
  "WH-FRA": "WH-FRA",
  "CUST-EU-HUB": "EU HUB",
};

const LABEL_DY: Record<string, number> = {
  "SUP-CHN-A": -18,
  "SUP-CHN-B": -17,
  "SUP-IND-A": 22,
  "SUP-EU-A": -18,
  "PLANT-HYD": 24,
  "PLANT-MUM": 24,
  "PORT-JNPT": -18,
  "PORT-SUEZ": -18,
  "WH-BOM": -17,
  "WH-DEL": 23,
  "WH-FRA": -18,
  "CUST-EU-HUB": 26,
};

export interface MapFocus {
  affectedNodes: Set<string>;
  blockedNodes: Set<string>;
  affectedLanes: Set<string>;
  redSeaLanes: Set<string>;
  hitShipmentLanes: Set<string>;
}

export function collectFocus(
  network: NetworkSnapshot | null,
  affectedNodes: string[],
  trace: { graph?: { blocked_nodes: string[]; disrupted_lanes: string[] } } | undefined,
  affectedShipments: string[],
): MapFocus {
  const affectedShipmentSet = new Set(affectedShipments);
  const hitLanes = new Set(
    (network?.shipments ?? [])
      .filter((s) => affectedShipmentSet.has(s.id))
      .map((s) => s.lane_id),
  );
  return {
    affectedNodes: new Set(affectedNodes),
    blockedNodes: new Set(trace?.graph?.blocked_nodes ?? []),
    affectedLanes: new Set(trace?.graph?.disrupted_lanes ?? []),
    redSeaLanes: new Set((network?.lanes ?? []).filter((l) => l.red_sea_exposed).map((l) => l.id)),
    hitShipmentLanes: hitLanes,
  };
}

export function NetworkMap({
  network,
  focus,
  title,
}: {
  network: NetworkSnapshot | null;
  focus: MapFocus;
  title: string;
}): JSX.Element {
  if (!network) {
    return (
      <div className="empty">
        <div className="empty__title">Network not loaded</div>
        <div>Run the scenario to load the seeded 12-node network.</div>
      </div>
    );
  }

  const consignmentsByLane = new Map<string, number>();
  for (const shipment of network.shipments) {
    consignmentsByLane.set(shipment.lane_id, (consignmentsByLane.get(shipment.lane_id) ?? 0) + 1);
  }

  const affectedSummary = [
    `${focus.affectedNodes.size} of ${network.nodes.length} nodes affected`,
    `${focus.affectedLanes.size} of ${network.lanes.length} lanes disrupted`,
    focus.blockedNodes.size > 0
      ? `${[...focus.blockedNodes].join(", ")} out of service`
      : "no node out of service",
  ].join("; ");

  return (
    <div>
      {/*
       * role="group", not role="img". An image is atomic — assistive tech is
       * entitled to flatten everything inside it, which would make the
       * per-node controls unreachable while still leaving them in the tab
       * order. That combination is what axe-core reports as nested-interactive.
       * A group can legitimately contain focusable members.
       */}
      <svg
        className="netmap"
        viewBox={`0 0 ${VIEW_W} ${VIEW_H}`}
        role="group"
        aria-label={`Supply network map. ${title}. ${affectedSummary}.`}
        preserveAspectRatio="xMidYMid meet"
      >
        <defs>
          <pattern id="grid" width="40" height="40" patternUnits="userSpaceOnUse">
            <path d="M40 0 L0 0 0 40" fill="none" stroke="var(--border-subtle)" strokeWidth="0.5" />
          </pattern>
        </defs>
        <rect width={VIEW_W} height={VIEW_H} fill="url(#grid)" opacity="0.55" />

        {/* ---- lanes, base layer ---- */}
        <g>
          {network.lanes.map((lane) => {
            const points = lane.path
              .map((nodeId) => POSITIONS[nodeId])
              .filter((p): p is [number, number] => Boolean(p))
              .map(([x, y]) => `${x},${y}`)
              .join(" ");
            const affected = focus.affectedLanes.has(lane.id);
            const blocked = affected && focus.blockedNodes.size > 0 && lane.red_sea_exposed;
            const hit = focus.hitShipmentLanes.has(lane.id);
            const className = [
              "netmap__lane",
              affected ? "netmap__lane--affected" : "",
              blocked ? "netmap__lane--blocked" : "",
              hit && !blocked ? "netmap__lane--hit" : "",
            ]
              .filter(Boolean)
              .join(" ");
            return (
              <polyline
                key={lane.id}
                className={className}
                points={points}
                opacity={affected ? 1 : 0.42}
              >
                <title>
                  {lane.name} · {lane.mode} · {lane.transit_days}d
                  {lane.red_sea_exposed ? " · Red Sea exposed" : ""}
                  {affected ? " · DISRUPTED by this event" : ""}
                </title>
              </polyline>
            );
          })}
        </g>

        {/* ---- lane consignment badges ---- */}
        <g>
          {network.lanes.map((lane) => {
            const count = consignmentsByLane.get(lane.id) ?? 0;
            if (count === 0) return null;
            const mid = lane.path[Math.floor(lane.path.length / 2)];
            const pos = mid ? POSITIONS[mid] : undefined;
            if (!pos) return null;
            const [x, y] = pos;
            return (
              <g key={`${lane.id}-badge`} transform={`translate(${x + 16}, ${y - 20})`}>
                <circle r="9" fill="var(--bg-raised)" stroke="var(--border-strong)" />
                <text
                  y="3.5"
                  textAnchor="middle"
                  fontSize="9"
                  fontFamily="var(--font-mono)"
                  fill="var(--text-secondary)"
                >
                  {count}
                </text>
                <title>
                  {count} consignment{count === 1 ? "" : "s"} on {lane.name}
                </title>
              </g>
            );
          })}
        </g>

        {/* ---- nodes ---- */}
        <g>
          {network.nodes.map((node) => {
            const pos = POSITIONS[node.id];
            if (!pos) return null;
            const [x, y] = pos;
            const r = TYPE_RADIUS[node.type] ?? 11;
            const affected = focus.affectedNodes.has(node.id);
            const blocked = focus.blockedNodes.has(node.id);
            const color = TYPE_COLOR[node.type] ?? "var(--text-muted)";
            const labelClass = [
              "netmap__node-label",
              affected ? "netmap__node-label--affected" : "",
              blocked ? "netmap__node-label--closed" : "",
            ]
              .filter(Boolean)
              .join(" ");

            return (
              <g
                key={node.id}
                className="netmap__node"
                transform={`translate(${x}, ${y})`}
                tabIndex={0}
                /*
                 * role="button" was a lie: these nodes have no activation
                 * handler, so a screen reader would announce "button" and
                 * promise a click that does nothing. They stay focusable so a
                 * keyboard user can inspect the network node by node, but they
                 * carry no widget role — their accessible name describes them,
                 * which is exactly what focusing one is for.
                 */
                aria-label={`${node.name} (${node.type})${
                  blocked ? ", out of service" : affected ? ", affected" : ""
                }`}
              >
                {affected && (
                  <circle
                    r={r + 8}
                    fill="none"
                    stroke={blocked ? "var(--danger)" : "var(--warn)"}
                    strokeWidth="1.5"
                    strokeDasharray="3 3"
                    opacity="0.85"
                  />
                )}
                <circle
                  r={r}
                  fill={blocked ? "var(--danger-dim)" : "var(--bg-panel-2)"}
                  stroke={blocked ? "var(--danger)" : color}
                  strokeWidth={affected ? 2.6 : 1.6}
                />
                <text
                  y="3.5"
                  textAnchor="middle"
                  fontSize={node.type === "customer" ? 11 : 9}
                  fontFamily="var(--font-mono)"
                  fill={blocked ? "var(--danger)" : color}
                  pointerEvents="none"
                >
                  {blocked ? "✕" : node.type === "customer" ? "◆" : "●"}
                </text>
                <text
                  className={labelClass}
                  y={LABEL_DY[node.id] ?? -18}
                  textAnchor="middle"
                >
                  {SHORT_LABEL[node.id] ?? node.id}
                </text>
                <title>
                  {node.name} · {node.city}, {node.country} · {node.type} · temp{" "}
                  {node.temp_class} · risk {node.risk_score}
                  {blocked ? " · OUT OF SERVICE" : affected ? " · affected" : ""}
                </title>
              </g>
            );
          })}
        </g>
      </svg>

      <div className="netmap__legend" role="list" aria-label="Map legend">
        <span className="netmap__legend-item" role="listitem">
          <span className="swatch" style={{ background: "var(--violet)" }} aria-hidden="true" />
          Supplier
        </span>
        <span className="netmap__legend-item" role="listitem">
          <span className="swatch" style={{ background: "var(--accent)" }} aria-hidden="true" />
          Plant
        </span>
        <span className="netmap__legend-item" role="listitem">
          <span className="swatch" style={{ background: "var(--info)" }} aria-hidden="true" />
          Port / chokepoint
        </span>
        <span className="netmap__legend-item" role="listitem">
          <span className="swatch" style={{ background: "var(--ok)" }} aria-hidden="true" />
          Warehouse
        </span>
        <span className="netmap__legend-item" role="listitem">
          <span className="swatch" style={{ background: "var(--warn)" }} aria-hidden="true" />
          Customer market
        </span>
        <span className="netmap__legend-item" role="listitem">
          <span
            className="swatch swatch--line"
            style={{ borderTopColor: "var(--danger)" }}
            aria-hidden="true"
          />
          Severed by closure
        </span>
        <span className="netmap__legend-item" role="listitem">
          <span
            className="swatch swatch--line"
            style={{ borderTopColor: "var(--warn)" }}
            aria-hidden="true"
          />
          Disrupted lane
        </span>
        <span className="netmap__legend-item" role="listitem">
          <span
            className="swatch swatch--line"
            style={{ borderTopColor: "var(--accent)" }}
            aria-hidden="true"
          />
          Carries affected consignment
        </span>
        <span className="netmap__legend-item" role="listitem">
          <Provenance kind="deterministic" text="layout is fixed, not force-directed" />
        </span>
      </div>
    </div>
  );
}
