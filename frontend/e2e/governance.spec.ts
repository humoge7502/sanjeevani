import { expect, test } from "@playwright/test";

import {
  EXECUTE_BUTTON,
  approvePlan,
  executePlan,
  expectStage,
  resetToStart,
  runHeroScenario,
} from "./helpers";

/**
 * Governance from the browser's point of view.
 *
 * The red-team suite in `tests/redteam/` attacks the backend's Python API. This
 * spec attacks the *running HTTP boundary* — the surface a curious judge with
 * devtools open would use. The claim under test: the UI is an interface, not the
 * security boundary, so forging the request still fails.
 */

test.describe("governance cannot be bypassed from the client", () => {
  test("execution is refused by the backend before any approval exists", async ({ page }) => {
    await page.goto("/");
    await runHeroScenario(page);

    await expect(page.getByRole("button", { name: EXECUTE_BUTTON })).toBeDisabled();

    const response = await page.request.post("/api/plans/PLAN-001/execute");
    expect(response.ok()).toBe(false);

    const body = (await response.json()) as { error?: string; message?: string };
    expect(body.error, "a refusal must name its error code").toBeTruthy();
    expect(JSON.stringify(body).toLowerCase()).toContain("approv");
  });

  test("approval by an actor without the required role is refused", async ({ page }) => {
    await page.goto("/");
    await runHeroScenario(page);

    // The UI pre-empts this: anil.deshpande is a planner, the plan needs
    // supply_planning_head, so the control disables and explains why.
    await page.getByLabel("Acting as").selectOption("anil.deshpande");
    await expect(page.getByRole("button", { name: /Approve & authorize/ })).toBeDisabled();
    await expect(page.getByText(/this plan requires/)).toBeVisible();

    // And the backend refuses the same request when the UI is bypassed.
    const response = await page.request.post("/api/plans/PLAN-001/approve", {
      data: { actor_id: "anil.deshpande", rationale: "forged" },
    });
    expect(response.ok()).toBe(false);
    await expectStage(page, "APPROVE", "awaiting");
  });

  test("an unknown actor holds no role and is refused", async ({ page }) => {
    await page.goto("/");
    await runHeroScenario(page);

    const response = await page.request.post("/api/plans/PLAN-001/approve", {
      data: { actor_id: "unknown.actor", rationale: "forged" },
    });
    expect(response.ok()).toBe(false);
    await expectStage(page, "APPROVE", "awaiting");
  });

  test("rejection closes the path to execution", async ({ page }) => {
    await page.goto("/");
    await runHeroScenario(page);

    await page.getByRole("button", { name: /Reject/ }).click();
    await expect(page.getByRole("status").filter({ hasText: "Rejected" }).first()).toBeVisible();

    await expect(page.getByRole("button", { name: EXECUTE_BUTTON })).toBeDisabled();

    const response = await page.request.post("/api/plans/PLAN-001/execute");
    expect(response.ok()).toBe(false);
  });

  test("double execution replays the original receipt instead of posting again", async ({
    page,
  }) => {
    await page.goto("/");
    await runHeroScenario(page);
    await approvePlan(page);
    await executePlan(page);

    const firstId = await page.getByText(/EXEC-\d+/).first().innerText();
    const firstCorr = await page.getByText(/CORR-\d+/).first().innerText();

    // The UI will not even offer a second run once the plan is COMPLETED — the
    // execute control disables. So the real duplicate risk is a forged or
    // retried HTTP call, which is what this exercises.
    await expect(page.getByRole("button", { name: EXECUTE_BUTTON })).toBeDisabled();

    const retry = await page.request.post("/api/plans/PLAN-001/execute");
    if (retry.ok()) {
      const body = (await retry.json()) as {
        execution?: { receipt?: { execution_id?: string | null; correlation_id?: string | null } };
      };
      expect(body.execution?.receipt?.execution_id).toEqual(firstId);
      expect(body.execution?.receipt?.correlation_id).toEqual(firstCorr);
    } else {
      // Refusing outright is an equally valid "did not double-post" answer.
      expect(retry.status()).toBeGreaterThanOrEqual(400);
    }

    // Whatever the API chose, exactly one execution is on the record.
    await page.reload();
    await expect(page.getByText(/EXEC-\d+/).first()).toHaveText(firstId);
    await expect(page.getByText(/CORR-\d+/).first()).toHaveText(firstCorr);
  });

  test("a page reload mid-flow does not re-execute anything", async ({ page }) => {
    await page.goto("/");
    await runHeroScenario(page);
    await approvePlan(page);
    await executePlan(page);

    const before = await page.getByText(/EXEC-\d+/).first().innerText();

    await page.reload();
    await expect(page.getByRole("heading", { name: "SANJEEVANI" })).toBeVisible();
    await expect(page.getByTestId("coverage-LOOP_CLOSED")).toHaveAttribute("data-present", "true");

    expect(await page.getByText(/EXEC-\d+/).first().innerText()).toEqual(before);
  });

  test("no approval survives a reset", async ({ page }) => {
    await page.goto("/");
    await runHeroScenario(page);
    await approvePlan(page);
    await expect(page.getByRole("button", { name: EXECUTE_BUTTON })).toBeEnabled();

    await resetToStart(page);

    // Authority must not leak across runs; a stale approval is exactly the ghost
    // state that makes a demo execute something nobody authorized.
    const response = await page.request.post("/api/plans/PLAN-001/execute");
    expect(response.ok()).toBe(false);
  });
});
