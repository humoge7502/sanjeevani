/**
 * API client.
 *
 * Deliberately tiny: `fetch` plus a typed error path. There is no query cache
 * and no retry loop, because every endpoint is a local deterministic call that
 * returns in milliseconds. Adding a data-fetching library here would be
 * dependency bloat for a problem that does not exist.
 *
 * Governance errors arrive as structured 4xx/409 bodies and are surfaced
 * verbatim, because "why did this not execute?" is the question the product
 * exists to answer.
 */

import type {
  ApiError,
  DemoState,
  GovernancePayload,
  NetworkSnapshot,
  PolicyCatalogueEntry,
} from "./types";

const BASE = import.meta.env["VITE_API_BASE"] ?? "";

export class GovernanceRefusal extends Error {
  readonly code: string;
  readonly detail: Record<string, unknown>;
  readonly enforcedBy: string | undefined;

  constructor(payload: ApiError) {
    super(payload.message);
    this.name = "GovernanceRefusal";
    this.code = payload.error;
    this.detail = payload.detail ?? {};
    this.enforcedBy = payload.governance?.enforced_by;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${BASE}${path}`, {
      headers: { "Content-Type": "application/json" },
      ...init,
    });
  } catch (cause) {
    throw new Error(
      `Cannot reach the SANJEEVANI backend at ${BASE || "the dev proxy"}. ` +
        `Start it with: python -m uvicorn backend.api.app:app --port 8787 ` +
        `(or just run: bash scripts/dev.sh). (${String(cause)})`,
    );
  }

  // A proxy or a crashed backend can answer with an HTML error page; a hard
  // JSON.parse there would surface as a bare SyntaxError and hide the status
  // code — the one thing the operator needs. Parse defensively: error
  // responses fall back to a legible envelope that keeps the HTTP status, and
  // a non-JSON *success* is its own clear error rather than a SyntaxError.
  const text = await response.text();
  let body: unknown = null;
  if (text) {
    try {
      body = JSON.parse(text);
    } catch {
      if (response.ok) {
        throw new Error(
          `Backend at ${BASE || "the dev proxy"} returned ${response.status} with a non-JSON body where JSON was expected.`,
        );
      }
      body = {
        error: "NON_JSON_RESPONSE",
        message: `Backend returned ${response.status} with a non-JSON body.`,
      };
    }
  }

  if (!response.ok) {
    // `||` not `??` on purpose: a body with `message: ""` — or no body at all,
    // where statusText is empty on HTTP/2 — must still yield a legible
    // message that includes the status code.
    const payload = (body ?? {}) as Partial<ApiError>;
    throw new GovernanceRefusal({
      error: payload.error || "HTTP_ERROR",
      message: payload.message || `Request failed with ${response.status}`,
      detail: payload.detail,
      governance: payload.governance,
    });
  }
  return body as T;
}

export const api = {
  health: () =>
    request<{
      status: string;
      contract_version: string;
      offline: boolean;
      deterministic_seed: number;
      clock_frozen: boolean;
      llm_enabled: boolean;
    }>("/api/health"),

  state: () => request<DemoState>("/api/state"),

  network: () => request<NetworkSnapshot>("/api/network"),

  policies: () =>
    request<{
      meta: Record<string, unknown>;
      rules: PolicyCatalogueEntry[];
      provenance_labels: Record<string, string>;
    }>("/api/policies"),

  demoScenario: () => request<Record<string, unknown>>("/api/demo/scenario"),

  boundaries: () => request<DemoState["mock_boundaries"]>("/api/mock-boundaries"),

  audit: () =>
    request<{
      timeline: DemoState["ledger"]["timeline"];
      coverage: DemoState["ledger"]["coverage"];
      chain: DemoState["ledger"]["chain"];
      records: unknown[];
    }>("/api/audit"),

  impact: (horizonDays?: number) =>
    request<{ primary?: unknown; all?: unknown; impact?: unknown }>(
      horizonDays ? `/api/impact?horizon_days=${horizonDays}` : "/api/impact",
    ),

  governance: (planId: string) =>
    request<{ governance: GovernancePayload; state_machine: Record<string, unknown> }>(
      `/api/plans/${encodeURIComponent(planId)}/governance`,
    ),

  reset: () =>
    request<{ reset: boolean; reset_at: string; note: string }>("/api/demo/reset", {
      method: "POST",
    }),

  run: () => request<DemoState>("/api/demo/run", { method: "POST" }),

  approve: (planId: string, actorId: string, rationale?: string) =>
    request<DemoState>(`/api/plans/${encodeURIComponent(planId)}/approve`, {
      method: "POST",
      body: JSON.stringify({ actor_id: actorId, rationale: rationale ?? null }),
    }),

  reject: (planId: string, actorId: string, rationale?: string) =>
    request<DemoState>(`/api/plans/${encodeURIComponent(planId)}/reject`, {
      method: "POST",
      body: JSON.stringify({ actor_id: actorId, rationale: rationale ?? null }),
    }),

  requestReview: (planId: string, actorId: string, rationale?: string) =>
    request<DemoState>(`/api/plans/${encodeURIComponent(planId)}/request-review`, {
      method: "POST",
      body: JSON.stringify({ actor_id: actorId, rationale: rationale ?? null }),
    }),

  execute: (planId: string) =>
    request<DemoState>(`/api/plans/${encodeURIComponent(planId)}/execute`, {
      method: "POST",
    }),
};
