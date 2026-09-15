/**
 * Wire types for the SANJEEVANI API.
 *
 * These mirror the backend payloads. Where a field is optional on the wire it is
 * optional here too, so the UI is forced to handle the absent case rather than
 * assume a happy path.
 */

export type Severity = "LOW" | "MEDIUM" | "HIGH" | "CRITICAL";
export type RiskTier = "L1" | "L2" | "L3" | "L4";
export type CheckOutcome = "PASS" | "FAIL" | "WARN" | "ERROR";
export type LoopState =
  | "done"
  | "active"
  | "awaiting"
  | "blocked"
  | "pending";

export interface VerifiedEvent {
  event_id: string;
  type: string;
  severity: Severity;
  confidence: number;
  timestamp: string;
  source: string;
  target: string;
  verification: {
    rule: string;
    description?: string;
    reason?: string;
    confidence_math?: string;
    source_classes?: string[];
    fresh_source_classes?: string[];
    corroborated?: boolean;
    peak_delta_c?: number;
    trace?: ExcursionAnalysis | null;
    deduplicated_signals?: string[];
    provenance?: { url: string; published_utc: string; retrieved_utc: string };
    freshness?: string;
    signal_age_hours?: number;
  };
  contributing_signals: string[];
}

export interface ExcursionAnalysis {
  shipment_id: string;
  device_id: string;
  sample_count: number;
  peak_temp_c: number;
  min_temp_c: number;
  limit_max_c: number;
  peak_excess_c: number;
  minutes_above_limit: number;
  degree_minutes: number;
  samples_above_limit: number;
  breached: boolean;
  condemn_ratio: number;
  method: {
    model: string;
    labelled_as: string;
    band: string;
    ratio_formula: string;
    inputs: Record<string, number>;
    thresholds: Record<string, number>;
  };
}

export interface ConsignmentRow {
  shipment_id: string;
  sku_id: string;
  sku_name: string;
  lane_id: string;
  market: string;
  current_node: string;
  value_usd: number;
  coverage_days: number;
  remaining_fraction: number;
  delay_days: number;
  disrupted_days: number;
  shortfall_days: number;
  p_stockout: number;
  condemn_ratio: number;
  value_at_risk_usd: number;
  reasons: string[];
}

export interface ServiceLevelRow {
  market: string;
  service_target: number;
  achieved_service_level: number;
  breach_points: number;
  value_weight: number;
  formula: string;
}

export interface ImpactPayload {
  event_id: string;
  affected_nodes: string[];
  affected_shipments: string[];
  revenue_at_risk: number;
  stockout_probability: number;
  service_level_risk: number;
  affected_products: string[];
  scenario_id: string;
  horizon_days: number;
  related_event_ids: string[];
  trace: {
    method: string;
    labelled_as: string;
    inputs: Record<string, unknown>;
    disruption: Record<string, unknown>;
    graph: {
      blocked_nodes: string[];
      disrupted_lanes: string[];
      nodes_on_disrupted_lanes: string[];
      downstream_of_blocked: string[];
      upstream_of_blocked: string[];
      affected_nodes: string[];
      formula: string;
    };
    aggregation: {
      total_consignment_value_usd: number;
      revenue_at_risk_usd: number;
      revenue_at_risk_formula: string;
      stockout_probability_formula: string;
      service_level_risk_formula: string;
    };
    consignments: ConsignmentRow[];
    service_level_by_market: ServiceLevelRow[];
    thresholds: Record<string, unknown>;
    config_keys: string[];
  };
  probability_weight?: number;
}

export interface ScenarioPayload {
  scenario_id: string;
  event_id: string;
  name: string;
  horizon_days: number;
  duration_days: number;
  transit_delay_days: number;
  capacity_factor: number;
  probability_weight: number;
  confidence: number;
  affected_lanes: string[];
  narrative: string;
  parameters_source: {
    event_id: string;
    origin: string;
    clamped: Record<string, boolean>;
    bounds: Record<string, number[]>;
  };
}

export interface CandidateSignal {
  candidate_id: string;
  event_type: string;
  target: string;
  severity: Severity;
  source_class: string;
  source_id: string;
  source_reliability: number;
  raw_confidence: number;
  provenance: { url: string; published_utc: string; retrieved_utc: string };
  age_hours: number;
  freshness: string;
  claim: Record<string, unknown>;
  entities: string[];
  headline: string;
  status: string;
  reasons: string[];
}

export interface M1Snapshot {
  agent_chain: string[];
  generated_at: string;
  events: VerifiedEvent[];
  scenarios: ScenarioPayload[];
  impacts: ImpactPayload[];
  primary_impact: ImpactPayload;
  primary_horizon_days: number;
  watchlist: CandidateSignal[];
  rejected: Array<{
    event_type: string;
    target: string;
    verification: Record<string, unknown>;
    signals: string[];
  }>;
  deduplicated: Array<{
    candidate_id: string;
    signal_id: string;
    reason: string;
    source_class: string;
    duplicate_of: string;
    window_hours: number;
    freshness: string;
    note: string;
  }>;
  rules: Record<string, string>;
}

export interface RecoveryPlanPayload {
  plan_id: string;
  strategy: string;
  cost: number;
  service_level: number;
  resilience_score: number;
  temperature_risk: number;
  recovery_time_hours: number;
  compliance_status: string;
  event_id?: string | null;
  impacted_skus: string[];
  impacted_lanes: string[];
  rationale?: string | null;
  alternatives: Array<{
    plan_id: string;
    strategy: string;
    cost: number;
    service_level: number;
    resilience_score: number;
    temperature_risk: number;
    recovery_time_hours: number;
    recommended: boolean;
  }>;
  source: "optimizer" | "fixture";
  contract_version: string;
}

export interface PolicyCheck {
  rule_id: string;
  name: string;
  dimension: string;
  provenance: string;
  provenance_label: string;
  severity: string;
  outcome: CheckOutcome;
  threshold: unknown;
  actual: unknown;
  message: string;
  evidence: Record<string, unknown>;
}

export interface ComplianceRecord {
  plan_id: string;
  strategy: string;
  risk_tier: RiskTier;
  tier: string;
  required_role: string | null;
  approval_required: boolean;
  rulebook_version: string;
  review_status: string;
  scope: {
    excursed_shipments: string[];
    temperature_sensitive: boolean;
    critical_scope: boolean;
    sku_ids: string[];
  };
  checks: {
    plan_id: string;
    tier: string;
    risk_tier: RiskTier;
    required_role: string | null;
    approval_required: boolean;
    blocking: boolean;
    rulebook_version: string;
    review_status: string;
    passed: PolicyCheck[];
    failed: PolicyCheck[];
    warned: PolicyCheck[];
    errored: PolicyCheck[];
  };
  summary: {
    passed: number;
    failed: number;
    warned: number;
    errored: number;
    blocking: boolean;
  };
  // Values match the frozen RecoveryPlan schema in docs/evidence/contracts/.
  compliance_status: "PASSED" | "FAILED" | "REVIEW";
  rationale: string;
  disclaimer: string;
  state: string;
}

export interface ApprovalRequest {
  plan_id: string;
  strategy: string;
  required_role: string | null;
  risk_tier: RiskTier;
  approval_required: boolean;
  cost: number;
  service_level: number;
  resilience_score: number;
  temperature_risk: number;
  recovery_time_hours: number;
  rationale?: string | null;
  plan_source: string;
  alternatives: RecoveryPlanPayload["alternatives"];
  compliance: {
    status: string;
    summary: ComplianceRecord["summary"];
    warnings: string[];
    failed: string[];
  };
  impact: {
    event_id: string;
    revenue_at_risk: number;
    stockout_probability: number;
    service_level_risk: number;
    affected_nodes: string[];
    affected_shipments: string[];
    affected_products: string[];
    horizon_days: number;
  };
  what_will_happen: Array<{
    system: string;
    label: string;
    mock: boolean;
    action: string;
  }>;
  withhold: string;
}

export interface ExecutionAction {
  system: string;
  label?: string;
  status: string;
  operation?: string;
  mock?: boolean;
  summary?: string;
  reason?: string;
  error?: { system: string; error: string; message: string };
  response?: Record<string, unknown>;
}

export interface ExecutionPayload {
  receipt: {
    plan_id: string;
    ibp: string;
    tm: string;
    ariba: string;
    timestamp: string;
    execution_id: string | null;
    correlation_id: string | null;
    actions: ExecutionAction[];
    mock: boolean;
    contract_version: string;
  };
  actions: ExecutionAction[];
  compensating_actions: Array<{
    system: string;
    action: string;
    description: string;
    available: boolean;
  }>;
  failure: { system: string; error: string; message: string } | null;
  replayed: boolean;
  replay_note?: string;
}

export interface LearningPayload {
  outcome: {
    plan_id: string;
    predicted: Record<string, number>;
    actual: Record<string, number>;
    delta: Record<string, number>;
    learning_note: string;
    event_id: string | null;
    calibration_signals: Array<{
      key: string;
      observation: string;
      proposal: string;
      requires_human_approval: boolean;
      auto_applied: boolean;
    }>;
    reconciliation_status: string;
    retraining_performed: boolean;
    contract_version: string;
  };
  scorecard: {
    status: string;
    plan_id: string;
    strategy: string;
    rows: Array<{
      metric: string;
      predicted: number;
      actual: number;
      absolute_delta: number;
      percent_delta: number | null;
      direction: string;
    }>;
    summary: {
      metrics_compared: number;
      better_than_predicted: number;
      worse_than_predicted: number;
      calibration_signals: number;
      retraining_performed: boolean;
    };
    method: string;
  };
  calibration_signals: LearningPayload["outcome"]["calibration_signals"];
  observation_source: Record<string, unknown>;
}

export interface GovernancePayload {
  plan_id: string;
  state: string;
  risk_tier: RiskTier;
  required_role: string | null;
  approval_required: boolean;
  terminal: boolean;
  can_execute: boolean;
  blocks_execution: boolean;
  approval: Record<string, unknown> | null;
  receipt: Record<string, unknown> | null;
  blocked_reason: string | null;
  review_notes: Array<{ by: string; role: string; note: string; timestamp: string }>;
  history: Array<{
    seq: number;
    from: string;
    to: string;
    actor: string;
    actor_role: string | null;
    reason: string;
    timestamp: string;
  }>;
}

export interface LedgerEntry {
  seq: number;
  stage: string;
  actor: string;
  action: string;
  result: string;
  timestamp: string;
  ids: Record<string, string>;
  hash: string;
  prev_hash: string;
}

export interface DemoState {
  run_id: string | null;
  started_at: string | null;
  scenario_clock: string;
  offline: boolean;
  m1: M1Snapshot | null;
  plan: RecoveryPlanPayload | null;
  ranked_plans: RecoveryPlanPayload[];
  plan_provider: {
    provider: string;
    source: string;
    reality: string;
    optimizer_implemented: boolean;
    owner: string;
    consumed_by: string;
    disclosure: string;
    fixture_version?: string;
  };
  compliance: ComplianceRecord | null;
  approval_request: ApprovalRequest | null;
  approval: {
    plan_id: string;
    approved: boolean;
    approved_by: string;
    timestamp: string;
    decision: string;
    role: string | null;
    rationale: string | null;
  } | null;
  governance: GovernancePayload | null;
  execution: ExecutionPayload | null;
  learning: LearningPayload | null;
  errors: Array<Record<string, string>>;
  ledger: {
    length: number;
    timeline: LedgerEntry[];
    coverage: {
      stages_expected: string[];
      stages_present: string[];
      stages_missing: string[];
      complete: boolean;
    };
    chain: {
      records: number;
      chain_enabled: boolean;
      intact: boolean;
      head: string | null;
      issues: Array<Record<string, unknown>>;
      scope: string;
    };
    stages_expected: string[];
  };
  sap: {
    boundary: string;
    real_integration: boolean;
    calls: Array<{
      system: string;
      operation: string;
      idempotency_key: string;
      correlation_id: string;
      status: string;
      timestamp: string;
      request: Record<string, unknown>;
      response: Record<string, unknown>;
    }>;
  };
  mock_boundaries: {
    real: string[];
    deterministic: string[];
    simulated: string[];
    mocked: string[];
    not_implemented: string[];
    ai_usage: string;
  };
  approvers: Array<{
    id: string;
    name: string;
    full_name: string;
    role: string;
    title: string;
    note?: string;
  }>;
  markets: Array<{ id: string; name: string; service_target: number }>;
}

export interface NetworkSnapshot {
  meta: Record<string, unknown>;
  nodes: Array<{
    id: string;
    type: string;
    name: string;
    country: string;
    city: string;
    temp_class: string;
    risk_score: number;
    closed: boolean;
    detail: Record<string, unknown>;
  }>;
  lanes: Array<{
    id: string;
    name: string;
    origin: string;
    destination: string;
    path: string[];
    mode: string;
    transit_days: number;
    cost_usd_per_pallet: number;
    temp_class: string;
    red_sea_exposed: boolean;
    direction: string;
  }>;
  shipments: Array<{
    id: string;
    sku_id: string;
    lane_id: string;
    market: string;
    units: number;
    pallets: number;
    current_node: string;
    destination: string;
    temp_class: string;
    value_usd: number;
    criticality: string;
  }>;
  skus: Array<{
    id: string;
    name: string;
    form: string;
    temp_class: string;
    unit_price_usd: number;
    coverage_days: number;
    criticality: string;
  }>;
}

export interface PolicyCatalogueEntry {
  id: string;
  name: string;
  dimension: string;
  provenance: string;
  provenance_label: string;
  severity: string;
  description: string;
  implemented: boolean;
}

export interface ApiError {
  error: string;
  message: string;
  detail?: Record<string, unknown>;
  governance?: { enforced_by: string; note: string };
}
