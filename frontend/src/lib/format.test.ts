/**
 * Frontend logic tests.
 *
 * Only pure modules are tested here. The components are deliberately thin and
 * delegate every judgement to these functions, so testing them covers the
 * decisions the UI makes without needing a DOM harness.
 */

import { describe, expect, it } from "vitest";

import {
  LOOP_STAGES,
  clockTime,
  currentStage,
  deriveLoop,
  humaniseKey,
  money,
  moneyExact,
  percent,
  riskLevel,
  shorten,
  signedMoney,
  signedPercent,
} from "./format";
import type { DemoState } from "./types";

/* ------------------------------------------------------------- formatting */

describe("money", () => {
  it("formats with two decimals by default", () => {
    expect(money(184000)).toBe("$184,000.00");
  });

  it("compacts large values", () => {
    expect(money(3908933.39, { compact: true })).toBe("$3.91M");
    expect(money(1_500_000_000, { compact: true })).toBe("$1.50B");
    expect(money(184000, { compact: true })).toBe("$184.0K");
  });

  it("returns an em dash rather than NaN for missing data", () => {
    expect(money(undefined)).toBe("—");
    expect(money(null)).toBe("—");
    expect(money(Number.NaN)).toBe("—");
    expect(moneyExact(undefined)).toBe("—");
  });
});

describe("percent", () => {
  it("converts a ratio to a percentage", () => {
    expect(percent(0.6321, 2)).toBe("63.21%");
    expect(percent(1)).toBe("100.0%");
    expect(percent(0)).toBe("0.0%");
  });

  it("handles missing data", () => {
    expect(percent(undefined)).toBe("—");
  });
});

describe("deltas", () => {
  it("signs percentages explicitly", () => {
    expect(signedPercent(4.08)).toBe("+4.1%");
    expect(signedPercent(-4.08)).toBe("-4.1%");
    expect(signedPercent(0)).toBe("0.0%");
    expect(signedPercent(null)).toBe("—");
  });

  it("signs money using a real minus sign and absolute value", () => {
    expect(signedMoney(7500)).toBe("+$7,500.00");
    expect(signedMoney(-7500)).toBe("−$7,500.00");
    expect(signedMoney(0)).toBe("$0.00");
  });
});

describe("clockTime", () => {
  it("extracts the time from an ISO string", () => {
    expect(clockTime("2026-09-30T06:00:00Z")).toBe("06:00:00");
  });

  it("degrades safely", () => {
    expect(clockTime(null)).toBe("—");
    expect(clockTime("not-a-date")).toBe("not-a-date");
  });
});

describe("shorten", () => {
  it("truncates with an ellipsis", () => {
    expect(shorten("abcdef", 3)).toBe("abc…");
    expect(shorten("abc")).toBe("abc");
    expect(shorten(null)).toBe("—");
  });
});

describe("humaniseKey", () => {
  it("turns snake case into a label", () => {
    expect(humaniseKey("revenue_at_risk_usd")).toBe("Revenue At Risk USD");
    expect(humaniseKey("service_level")).toBe("Service Level");
  });
});

/* ---------------------------------------------------------------- risk */

describe("riskLevel", () => {
  it("bands probability into a non-colour-only label", () => {
    expect(riskLevel(0.9).label).toBe("Critical");
    expect(riskLevel(0.6).label).toBe("High");
    expect(riskLevel(0.3).label).toBe("Elevated");
    expect(riskLevel(0.1).label).toBe("Low");
  });

  it("every band supplies a colour token and a tone class", () => {
    for (const p of [0.9, 0.6, 0.3, 0.1]) {
      const level = riskLevel(p);
      expect(level.barColor).toMatch(/^var\(--/);
      expect(level.tone).toMatch(/^badge--/);
    }
  });
});

/* ----------------------------------------------------------- loop model */

function makeState(overrides: Partial<DemoState> = {}): DemoState {
  return {
    run_id: null,
    started_at: null,
    scenario_clock: "2026-09-30T06:00:00Z",
    offline: true,
    m1: null,
    plan: null,
    ranked_plans: [],
    plan_provider: {
      provider: "FixtureRecoveryPlanProvider",
      source: "fixture",
      reality: "deterministic fixture",
      optimizer_implemented: false,
      owner: "M2",
      consumed_by: "M3",
      disclosure: "No MILP is solved.",
    },
    compliance: null,
    approval_request: null,
    approval: null,
    governance: null,
    execution: null,
    learning: null,
    errors: [],
    ledger: {
      length: 0,
      timeline: [],
      coverage: { stages_expected: [], stages_present: [], stages_missing: [], complete: false },
      chain: { records: 0, chain_enabled: true, intact: true, head: null, issues: [], scope: "" },
      stages_expected: [],
    },
    sap: { boundary: "", real_integration: false, calls: [] },
    mock_boundaries: {
      real: [],
      deterministic: [],
      simulated: [],
      mocked: [],
      not_implemented: [],
      ai_usage: "",
    },
    approvers: [],
    markets: [],
    ...overrides,
  };
}

describe("deriveLoop", () => {
  it("marks everything pending when nothing has run", () => {
    const loop = deriveLoop(null);
    for (const stage of LOOP_STAGES) {
      expect(loop[stage.key]?.state).toBe("pending");
    }
  });

  it("never marks the M2 stages as done, because they are not implemented", () => {
    const loop = deriveLoop(
      makeState({
        m1: { primary_impact: {} } as never,
        plan: { plan_id: "PLAN-001" } as never,
      }),
    );
    expect(loop["SIMULATE"]?.state).toBe("pending");
    expect(loop["OPTIMIZE"]?.state).toBe("pending");
    expect(loop["SIMULATE"]?.note).toContain("M2 fixture");
  });

  it("marks APPROVE as awaiting human authority at the gate", () => {
    const loop = deriveLoop(
      makeState({
        m1: { primary_impact: {} } as never,
        plan: { plan_id: "PLAN-001" } as never,
        compliance: { compliance_status: "PASSED", summary: { passed: 9 } } as never,
        governance: { state: "APPROVAL_REQUIRED" } as never,
      }),
    );
    expect(loop["POLICY"]?.state).toBe("done");
    expect(loop["APPROVE"]?.state).toBe("awaiting");
  });

  it("marks the loop blocked when compliance fails", () => {
    const loop = deriveLoop(
      makeState({
        m1: { primary_impact: {} } as never,
        plan: { plan_id: "PLAN-001" } as never,
        compliance: { compliance_status: "FAILED", summary: { failed: 3 } } as never,
        governance: { state: "COMPLIANCE_BLOCKED" } as never,
      }),
    );
    expect(loop["POLICY"]?.state).toBe("blocked");
    expect(loop["APPROVE"]?.state).toBe("blocked");
    expect(loop["APPROVE"]?.note).toContain("Never requested");
  });

  it("reports a partial execution as blocked rather than done", () => {
    const loop = deriveLoop(
      makeState({
        m1: { primary_impact: {} } as never,
        governance: { state: "FAILED" } as never,
        execution: { failure: { system: "tm", error: "X", message: "y" } } as never,
      }),
    );
    expect(loop["EXECUTE"]?.state).toBe("blocked");
  });

  it("reports a successful execution as done", () => {
    const loop = deriveLoop(
      makeState({
        m1: { primary_impact: {} } as never,
        governance: { state: "COMPLETED" } as never,
        execution: { failure: null } as never,
        learning: {} as never,
      }),
    );
    expect(loop["EXECUTE"]?.state).toBe("done");
    expect(loop["LEARN"]?.state).toBe("done");
  });
});

describe("currentStage", () => {
  it("starts at SENSE", () => {
    expect(currentStage(null)).toBe("SENSE");
  });

  it("points at the human gate when a decision is pending", () => {
    expect(
      currentStage(
        makeState({
          m1: { primary_impact: {} } as never,
          governance: { state: "APPROVAL_REQUIRED" } as never,
        }),
      ),
    ).toBe("APPROVE");
  });

  it("ends at LEARN when the loop is closed", () => {
    expect(currentStage(makeState({ governance: { state: "COMPLETED" } as never }))).toBe("LEARN");
  });
});

describe("LOOP_STAGES", () => {
  it("declares exactly one human stage", () => {
    const human = LOOP_STAGES.filter((s) => s.human);
    expect(human).toHaveLength(1);
    expect(human[0]?.key).toBe("APPROVE");
  });

  it("assigns the M2 stages to M2 so ownership is visible in the UI", () => {
    const m2 = LOOP_STAGES.filter((s) => s.owner === "M2").map((s) => s.key);
    expect(m2).toEqual(["SIMULATE", "OPTIMIZE"]);
  });

  it("covers all twelve published stages in order", () => {
    expect(LOOP_STAGES.map((s) => s.key)).toEqual([
      "SENSE",
      "VERIFY",
      "UNDERSTAND",
      "SCENARIO",
      "IMPACT",
      "SIMULATE",
      "OPTIMIZE",
      "POLICY",
      "APPROVE",
      "EXECUTE",
      "AUDIT",
      "LEARN",
    ]);
  });
});
