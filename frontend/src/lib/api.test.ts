/**
 * Tests for the API client's response-parsing contract.
 *
 * `request()` is the single funnel every API call goes through; these tests
 * pin the failure-mode behaviour that the hero journey and governance flows
 * depend on when the backend (or a proxy in front of it) misbehaves:
 *   - structured governance refusals arrive as typed GovernanceRefusal objects
 *   - non-JSON ERROR bodies (proxy 502 HTML pages, crashed backends) become a
 *     legible refusal instead of a raw SyntaxError
 *   - non-JSON SUCCESS bodies are a clear error, not silently-undefined data
 *
 * The network boundary is faked with `fetch` stubs; no server is started.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { GovernanceRefusal, api } from "./api";

function jsonResponse(body: string, status: number): Response {
  return new Response(body, {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("api request error handling", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn());
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("surfaces structured governance refusals as typed errors", async () => {
    (globalThis.fetch as ReturnType<typeof vi.fn>).mockResolvedValue(
      jsonResponse(
        JSON.stringify({
          error: "EXECUTION_BLOCKED",
          message: "Plan is not approved.",
          detail: { state: "APPROVAL_REQUIRED" },
        }),
        409,
      ),
    );

    const error = await api.execute("PLAN-001").catch((e: unknown) => e);
    expect(error).toBeInstanceOf(GovernanceRefusal);
    expect((error as GovernanceRefusal).code).toBe("EXECUTION_BLOCKED");
    expect((error as GovernanceRefusal).detail).toEqual({
      state: "APPROVAL_REQUIRED",
    });
  });

  it("turns non-JSON error bodies (proxy HTML pages) into a legible refusal", async () => {
    (globalThis.fetch as ReturnType<typeof vi.fn>).mockResolvedValue(
      new Response("<html><body>502 Bad Gateway</body></html>", {
        status: 502,
        headers: { "Content-Type": "text/html" },
      }),
    );

    const error = await api.state().catch((e: unknown) => e);
    expect(error).toBeInstanceOf(GovernanceRefusal);
    expect((error as GovernanceRefusal).code).toBe("NON_JSON_RESPONSE");
    expect((error as GovernanceRefusal).message).toContain("502");
  });

  it("rejects non-JSON success bodies with a clear error, not undefined data", async () => {
    (globalThis.fetch as ReturnType<typeof vi.fn>).mockResolvedValue(
      new Response("<html>booting</html>", {
        status: 200,
        headers: { "Content-Type": "text/html" },
      }),
    );

    await expect(api.state()).rejects.toThrow(/non-JSON body/);
  });

  it("keeps the HTTP status message when an error body is empty", async () => {
    (globalThis.fetch as ReturnType<typeof vi.fn>).mockResolvedValue(
      new Response("", { status: 503 }),
    );

    const error = await api.state().catch((e: unknown) => e);
    expect(error).toBeInstanceOf(GovernanceRefusal);
    expect((error as GovernanceRefusal).message).toContain("503");
  });

  it("resolves with parsed JSON on success", async () => {
    (globalThis.fetch as ReturnType<typeof vi.fn>).mockResolvedValue(
      jsonResponse(JSON.stringify({ status: "ok", ledger: { records: 41 } }), 200),
    );

    // health() declares the fields the UI consumes; the point of this test is
    // that the parsed body comes back as an object, not a string or undefined.
    const body = await api.health();
    expect(body).toBeTypeOf("object");
    expect(body.status).toBe("ok");
    expect((body as unknown as { ledger: { records: number } }).ledger.records).toBe(41);
  });
});
