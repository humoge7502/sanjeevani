import { expect, test } from "@playwright/test";

import {
  EXECUTE_BUTTON,
  approvePlan,
  collectPageErrors,
  executePlan,
  expectStage,
  resetToStart,
  runHeroScenario,
} from "./helpers";

/**
 * The hero journey, exercised through a real renderer.
 *
 * This spec answers "does the demo actually work on stage?". Everything asserted
 * here was observed in Chromium first; nothing is asserted against a guess about
 * what a component renders.
 */

const REVENUE_10D = "$3,908,933.39 · 10-day horizon";

test.describe("hero scenario", () => {
  test("loads in offline mode and offers the presenter path", async ({ page }) => {
    const errors = collectPageErrors(page);
    await page.goto("/");

    await expect(page.getByRole("heading", { name: "SANJEEVANI" })).toBeVisible();
    await expect(page.getByText("offline mode").first()).toBeVisible();
    await expect(page.getByText("Load the hero scenario")).toBeVisible();
    // The M2 boundary must be stated before a judge has to ask.
    await expect(page.getByText(/SIMULATE and OPTIMIZE are owned by Member 2/)).toBeVisible();

    expect(errors).toEqual([]);
  });

  test("runs the pipeline and stops at the human gate", async ({ page }) => {
    const errors = collectPageErrors(page);
    await page.goto("/");
    await runHeroScenario(page);

    // M1 completed, M2 is explicitly pending, M3 policy ran and stopped.
    await expectStage(page, "SENSE", "done");
    await expectStage(page, "IMPACT", "done");
    await expectStage(page, "SIMULATE", "pending");
    await expectStage(page, "OPTIMIZE", "pending");
    await expectStage(page, "POLICY", "done");
    await expectStage(page, "APPROVE", "awaiting");

    // A deterministic, traceable impact.
    await expect(page.getByText(REVENUE_10D)).toBeVisible();

    // The signal is verified with its confidence and provenance shown.
    await expect(page.getByText("EVT-001").first()).toBeVisible();
    await expect(page.getByText("COLD_CHAIN_EXCURSION").first()).toBeVisible();
    await expect(page.getByText(/0\.9603/).first()).toBeVisible();
    await expect(page.getByText("device://reefer-sensor-TVE-4417").first()).toBeVisible();

    // Credibility control: something was deliberately NOT acted on.
    await expect(
      page.getByText(/SUPPLIER_DISRUPTION on SUP-CHN-B was not verified/),
    ).toBeVisible();

    // M2 is consumed as a labelled fixture, never as an optimizer.
    await expect(page.getByText("REROUTE_MUMBAI_AIR").first()).toBeVisible();
    await expect(page.getByText("PLAN-001").first()).toBeVisible();

    // A5 gated the plan before a human was asked.
    await expect(page.getByRole("heading", { name: "Compliance evaluation" })).toBeVisible();
    await expect(page.getByText("9 passed · 0 failed · 1 warned")).toBeVisible();

    // Execution must be impossible until a human authorizes it.
    await expect(page.getByRole("button", { name: EXECUTE_BUTTON })).toBeDisabled();

    expect(errors).toEqual([]);
  });

  test("horizon switching re-derives the impact rather than relabelling it", async ({ page }) => {
    await page.goto("/");
    await runHeroScenario(page);

    await expect(page.getByText("Highlighting the 10-day scenario.")).toBeVisible();
    await expect(page.getByText(REVENUE_10D)).toBeVisible();
    const impactTenDay = await page.locator("#impact").innerText();

    await page.getByRole("button", { name: /^30-day/ }).click();
    await expect(page.getByText("Highlighting the 30-day scenario.")).toBeVisible();
    // A different horizon must produce a different number, not the same number
    // wearing a new label — in the impact panel AND in the headline banner.
    await expect(page.getByText(REVENUE_10D)).toHaveCount(0);
    expect(await page.locator("#impact").innerText()).not.toEqual(impactTenDay);

    await page.getByRole("button", { name: /^3-day/ }).click();
    await expect(page.getByText("Highlighting the 3-day scenario.")).toBeVisible();
    expect(await page.locator("#impact").innerText()).not.toEqual(impactTenDay);
  });

  test("one approval produces three governed posts, a receipt and a closed loop", async ({
    page,
  }) => {
    const errors = collectPageErrors(page);
    await page.goto("/");
    await runHeroScenario(page);

    await approvePlan(page);
    await expect(page.getByText("Approved — governed execution authorized")).toBeVisible();
    await expectStage(page, "APPROVE", "done");
    await expect(page.getByRole("button", { name: EXECUTE_BUTTON })).toBeEnabled();

    await executePlan(page);

    // The wow moment: one decision, three transactional posts, one receipt.
    await expect(page.getByText("Governed execution & receipts")).toBeVisible();
    await expect(page.getByText("Correlation ID", { exact: true })).toBeVisible();
    await expect(page.getByText(/CORR-\d+/).first()).toBeVisible();
    await expect(page.getByText(/EXEC-\d+/).first()).toBeVisible();
    await expect(page.getByText("SUCCESS").first()).toBeVisible();

    // Every governed post is on the record, and the loop is closed.
    for (const stage of [
      "EXECUTION_STARTED",
      "IBP_EXECUTED",
      "TM_EXECUTED",
      "ARIBA_EXECUTED",
      "EXECUTION_RECEIPTED",
      "OUTCOME_RECORDED",
      "LEARNING_SIGNAL_GENERATED",
      "LOOP_CLOSED",
    ]) {
      await expect(page.getByTestId(`coverage-${stage}`)).toHaveAttribute("data-present", "true");
    }

    // Audit: the decision is traceable and the hash chain verifies.
    await expect(page.getByText("chain intact")).toBeVisible();

    expect(errors).toEqual([]);
  });

  test("produces identical numbers on a second run, from reset", async ({ page }) => {
    await page.goto("/");

    await runHeroScenario(page);
    await expect(page.getByText(REVENUE_10D)).toBeVisible();

    await runHeroScenario(page);
    // Reproducibility is load-bearing in the dossier: if a second run differs,
    // the demo is not deterministic and the trust story weakens.
    await expect(page.getByText(REVENUE_10D)).toBeVisible();
  });

  test("reset returns the loop to its initial state", async ({ page }) => {
    await page.goto("/");
    await runHeroScenario(page);

    await resetToStart(page);
    await expectStage(page, "SENSE", "pending");
    await expectStage(page, "APPROVE", "pending");
    await expect(page.getByText(REVENUE_10D)).toHaveCount(0);
  });
});
