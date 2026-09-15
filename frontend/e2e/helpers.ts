import { expect, type Page } from "@playwright/test";

/**
 * Shared browser-test helpers.
 *
 * Two rules are enforced here rather than repeated in every spec:
 *
 * 1. Every spec starts from a reset. The backend holds ONE mutable demo state,
 *    so a spec that inherits the previous spec's approval would pass or fail
 *    for reasons unrelated to what it claims to test.
 * 2. Every spec fails if the page logged an error. A renderer that throws while
 *    still showing stale DOM is exactly the failure a screenshot misses.
 *
 * Waits target `data-state` on the loop strip rather than the visible label.
 * The labels are upper-cased by CSS and several of them ("Awaiting human",
 * "complete") collide with unrelated copy elsewhere on the page, which makes
 * text matching both ambiguous and wrong.
 */

export const EXECUTE_BUTTON = /Execute approved plan|Re-run execution/;

/** Assert a loop stage is in the expected state, then return. */
export async function expectStage(page: Page, key: string, state: string): Promise<void> {
  await expect(page.getByTestId(`loop-stage-${key}`)).toHaveAttribute("data-state", state);
}

/** Reset the backend, then wait for the loop to return to its initial state. */
export async function resetToStart(page: Page): Promise<void> {
  await page.getByRole("button", { name: "Reset" }).click();
  await expect(page.getByText("System reset")).toBeVisible();
  // The presenter prompt only renders when no scenario is loaded.
  await expect(page.getByText("Load the hero scenario")).toBeVisible();
}

/** Reset, then run the hero scenario, and wait for the human gate. */
export async function runHeroScenario(page: Page): Promise<void> {
  await resetToStart(page);
  await page.getByRole("button", { name: "Run hero scenario" }).first().click();
  // The pipeline has finished when it stops at the human gate.
  await expectStage(page, "APPROVE", "awaiting");
}

/** Approve the pending plan, optionally switching the acting approver first. */
export async function approvePlan(page: Page, actorId?: string): Promise<void> {
  if (actorId) {
    await page.getByLabel("Acting as").selectOption(actorId);
  }
  await page.getByRole("button", { name: /Approve & authorize/ }).click();
}

/** Execute the approved plan and wait until the audit reports the loop closed. */
export async function executePlan(page: Page): Promise<void> {
  await page.getByRole("button", { name: EXECUTE_BUTTON }).click();
  await expect(page.getByTestId("coverage-LOOP_CLOSED")).toHaveAttribute("data-present", "true");
}

/**
 * Attach console/page error collection.
 *
 * Returns the live array so the spec can assert it stayed empty at the end.
 */
export function collectPageErrors(page: Page): string[] {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(`pageerror: ${error.message}`));
  page.on("console", (message) => {
    if (message.type() !== "error") return;
    const text = message.text();
    // Browser-level noise for a request that was *expected* to fail. The specs
    // that provoke a refusal assert on the structured body instead.
    if (text.includes("Failed to load resource")) return;
    errors.push(`console.error: ${text}`);
  });
  return errors;
}
