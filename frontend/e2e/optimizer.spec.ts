import { expect, test } from "@playwright/test";

import { collectPageErrors, expectStage, resetToStart, runHeroScenario } from "./helpers";

/**
 * The optimizer surfaces, exercised in a real renderer.
 *
 * Runs under playwright.optimizer.config.ts, which boots the backend with
 * SANJEEVANI_PLAN_PROVIDER=optimizer. Everything asserted here was observed in
 * Chromium first; nothing is asserted against a guess about what renders.
 */

test.describe("optimizer mode", () => {
  test("the command center declares the optimizer before a judge has to ask", async ({
    page,
  }) => {
    const errors = collectPageErrors(page);
    await page.goto("/");

    await expect(page.getByText(/SIMULATE and OPTIMIZE are implemented and opt-in/)).toBeVisible();

    expect(errors).toEqual([]);
  });

  test("the solve produces an optimizer plan and marks both M2 stages done", async ({
    page,
  }) => {
    const errors = collectPageErrors(page);
    await page.goto("/");
    await runHeroScenario(page);

    await expectStage(page, "SIMULATE", "done");
    await expectStage(page, "OPTIMIZE", "done");
    await expectStage(page, "APPROVE", "awaiting");

    // The provider badge follows the live provider, and the plan id is the
    // content-derived optimizer id, not the fixture's PLAN-001.
    await expect(page.getByText("M2 optimizer").first()).toBeVisible();
    await expect(page.getByText(/PLAN-OPT-/).first()).toBeVisible();

    expect(errors).toEqual([]);
  });

  test("the Why-this-plan panel shows the solve, the DES cross-check and the counterfactuals", async ({
    page,
  }) => {
    const errors = collectPageErrors(page);
    await page.goto("/");
    await runHeroScenario(page);

    await expect(page.getByRole("heading", { name: "Why this plan" })).toBeVisible();
    await expect(page.getByText("The solve")).toBeVisible();
    await expect(page.getByText("The discrete-event cross-check").first()).toBeVisible();
    // The two models are reported side by side, never averaged.
    await expect(page.getByText("Twin recovery")).toBeVisible();
    await expect(page.getByText("DES recovery").first()).toBeVisible();
    // Every uniform policy carries its own DES verdict in the table.
    await expect(page.getByRole("columnheader", { name: "DES recovery" })).toBeVisible();
    await expect(page.getByText("Uniform policy").first()).toBeVisible();
    // The recommended plan's verdict is surfaced as a badge, not buried in the
    // trace (N-6): the optimized plan empties the corridor, so "not applicable"
    // is the truthful verdict -- and a contradiction, when one exists, leads
    // the panel as a warning (asserted implicitly by the fixture-spec guard
    // that no note--warn contradiction note renders here).
    await expect(page.getByText("not applicable").first()).toBeVisible();

    expect(errors).toEqual([]);
  });

  test("reset returns the optimizer run to its initial state", async ({ page }) => {
    await page.goto("/");
    await runHeroScenario(page);
    await resetToStart(page);

    await expectStage(page, "SIMULATE", "pending");
  });
});
