/**
 * Presentation helpers.
 *
 * Pure functions only — no React, no side effects — so they are directly unit
 * testable and reusable from any component. All the judgement about how a value
 * should be shown lives here rather than being scattered through JSX.
 */

import type { DemoState, LoopState, RiskTier, Severity } from "./types";

/* -------------------------------------------------------------- numbers */

export function money(value: number | null | undefined, opts?: { compact?: boolean }): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  const abs = Math.abs(value);
  if (opts?.compact) {
    if (abs >= 1_000_000_000) return `$${(value / 1_000_000_000).toFixed(2)}B`;
    if (abs >= 1_000_000) return `$${(value / 1_000_000).toFixed(2)}M`;
    if (abs >= 1_000) return `$${(value / 1_000).toFixed(1)}K`;
  }
  return `$${value.toLocaleString("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;
}

export function moneyExact(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return `$${value.toLocaleString("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;
}

export function percent(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return `${(value * 100).toFixed(digits)}%`;
}

export function num(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return value.toFixed(digits);
}

export function signedPercent(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  const sign = value > 0 ? "+" : "";
  return `${sign}${value.toFixed(digits)}%`;
}

export function signedMoney(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  const sign = value > 0 ? "+" : value < 0 ? "−" : "";
  return `${sign}${moneyExact(Math.abs(value))}`;
}

export function clockTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const match = /T(\d{2}:\d{2}:\d{2})/.exec(iso);
  return match?.[1] ?? iso;
}

export function shorten(value: string | null | undefined, length = 12): string {
  if (!value) return "—";
  return value.length <= length ? value : `${value.slice(0, length)}…`;
}

export function humaniseKey(key: string): string {
  return key
    .replace(/_/g, " ")
    .replace(/\b(id|usd|c)\b/gi, (m) => m.toUpperCase())
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

/* --------------------------------------------------- semantic classification */

export const SEVERITY_TONE: Record<Severity, string> = {
  LOW: "badge--ok",
  MEDIUM: "badge--info",
  HIGH: "badge--warn",
  CRITICAL: "badge--danger",
};

export const SEVERITY_GLYPH: Record<Severity, string> = {
  LOW: "•",
  MEDIUM: "◆",
  HIGH: "▲",
  CRITICAL: "■",
};

export const TIER_TONE: Record<RiskTier, string> = {
  L1: "badge--ok",
  L2: "badge--accent",
  L3: "badge--warn",
  L4: "badge--danger",
};

export const OUTCOME_TONE: Record<string, string> = {
  PASS: "badge--ok",
  FAIL: "badge--danger",
  WARN: "badge--warn",
  ERROR: "badge--violet",
  SUCCESS: "badge--ok",
  FAILED: "badge--danger",
  SKIPPED: "badge--plain",
  REPLAYED: "badge--info",
};

/** Non-colour-only outcome markers: every status also renders a glyph. */
export const OUTCOME_GLYPH: Record<string, string> = {
  PASS: "✓",
  FAIL: "✕",
  WARN: "!",
  ERROR: "⃠",
  SUCCESS: "✓",
  FAILED: "✕",
  SKIPPED: "–",
  REPLAYED: "↺",
  OK: "✓",
  DENIED: "✕",
  REVIEW: "!",
  BLOCKED: "■",
  DEDUPLICATED: "≡",
  REJECTED: "✕",
};

export function riskLevel(probability: number): {
  label: string;
  tone: string;
  barColor: string;
} {
  if (probability >= 0.75) {
    return { label: "Critical", tone: "badge--danger", barColor: "var(--danger)" };
  }
  if (probability >= 0.5) {
    return { label: "High", tone: "badge--warn", barColor: "var(--warn)" };
  }
  if (probability >= 0.25) {
    return { label: "Elevated", tone: "badge--info", barColor: "var(--info)" };
  }
  return { label: "Low", tone: "badge--ok", barColor: "var(--ok)" };
}

/* ------------------------------------------------------------- loop states */

export interface LoopStage {
  code: string;
  key: string;
  label: string;
  owner: "M1" | "M2" | "M3";
  human?: boolean;
}

/**
 * The eleven published loop stages. SIMULATE and OPTIMIZE are rendered as
 * carried-by-M2 rather than as implemented features, because they are not
 * implemented in this build.
 */
export const LOOP_STAGES: LoopStage[] = [
  { code: "01", key: "SENSE", label: "Sense", owner: "M1" },
  { code: "02", key: "VERIFY", label: "Verify", owner: "M1" },
  { code: "03", key: "UNDERSTAND", label: "Understand", owner: "M1" },
  { code: "04", key: "SCENARIO", label: "Scenario", owner: "M1" },
  { code: "05", key: "IMPACT", label: "Impact", owner: "M1" },
  { code: "06", key: "SIMULATE", label: "Simulate", owner: "M2" },
  { code: "07", key: "OPTIMIZE", label: "Optimize", owner: "M2" },
  { code: "08", key: "POLICY", label: "Policy", owner: "M3" },
  { code: "09", key: "APPROVE", label: "Approve", owner: "M3", human: true },
  { code: "10", key: "EXECUTE", label: "Execute", owner: "M3" },
  { code: "11", key: "AUDIT", label: "Audit", owner: "M3" },
  { code: "12", key: "LEARN", label: "Learn", owner: "M3" },
];

export interface LoopStatus {
  state: LoopState;
  note: string;
}

/**
 * Derive the loop's current position from the server state.
 *
 * This is presentation logic over server-owned truth: the frontend never
 * decides whether a stage is permitted, only how to draw it.
 */
export function deriveLoop(state: DemoState | null): Record<string, LoopStatus> {
  const out: Record<string, LoopStatus> = {};
  const stages = LOOP_STAGES.map((s) => s.key);
  for (const key of stages) {
    out[key] = { state: "pending", note: "Not reached" };
  }
  if (!state) return out;

  const hasRun = Boolean(state.m1);
  const govState = state.governance?.state ?? null;
  const complianceStatus = state.compliance?.compliance_status ?? null;

  if (hasRun) {
    for (const key of ["SENSE", "VERIFY", "UNDERSTAND", "SCENARIO", "IMPACT"]) {
      out[key] = { state: "done", note: "Completed (M1)" };
    }
  }

  if (hasRun) {
    out["SIMULATE"] = { state: "pending", note: "Owned by M2 — not implemented" };
    out["OPTIMIZE"] = { state: "pending", note: "Owned by M2 — not implemented" };
  }

  if (state.plan) {
    out["SIMULATE"] = { state: "pending", note: "M2 fixture supplied the plan" };
    out["OPTIMIZE"] = { state: "pending", note: "M2 fixture supplied the plan" };
  }

  if (complianceStatus === "PASSED") {
    out["POLICY"] = {
      state: "done",
      note: `${state.compliance?.summary.passed ?? 0} checks passed`,
    };
  } else if (complianceStatus === "FAILED") {
    out["POLICY"] = {
      state: "blocked",
      note: `${state.compliance?.summary.failed ?? 0} checks failed`,
    };
  }

  const awaiting =
    govState === "APPROVAL_REQUIRED" || govState === "REVIEW_REQUESTED";
  if (awaiting) {
    out["APPROVE"] = { state: "awaiting", note: "Human authority required" };
  } else if (govState && ["APPROVED", "EXECUTING", "EXECUTED", "AUDITED", "COMPLETED"].includes(govState)) {
    out["APPROVE"] = { state: "done", note: "Approved" };
  } else if (govState === "REJECTED") {
    out["APPROVE"] = { state: "blocked", note: "Rejected by human" };
  } else if (govState === "COMPLIANCE_BLOCKED" || govState === "POLICY_BLOCKED") {
    out["APPROVE"] = { state: "blocked", note: "Never requested — compliance blocked" };
  }

  if (state.execution) {
    const failed = Boolean(state.execution.failure);
    out["EXECUTE"] = failed
      ? { state: "blocked", note: "Partial failure — receipt issued" }
      : { state: "done", note: "IBP · TM · Ariba posted" };
  } else if (govState === "APPROVED") {
    out["EXECUTE"] = { state: "active", note: "Ready — awaiting execution call" };
  }

  if (state.ledger.length > 0) {
    out["AUDIT"] = {
      state: state.ledger.coverage.complete ? "done" : "active",
      note: `${state.ledger.length} records · chain ${state.ledger.chain.intact ? "intact" : "BROKEN"}`,
    };
  }
  if (state.learning) {
    out["LEARN"] = { state: "done", note: "Calibration signal generated" };
  }

  return out;
}

/** The single stage a presenter should look at right now. */
export function currentStage(state: DemoState | null): string {
  const loop = deriveLoop(state);
  for (const stage of LOOP_STAGES) {
    const status = loop[stage.key];
    if (status && (status.state === "awaiting" || status.state === "active" || status.state === "blocked")) {
      return stage.key;
    }
  }
  if (state?.governance?.state === "COMPLETED") return "LEARN";
  if (!state?.m1) return "SENSE";
  return "IMPACT";
}
