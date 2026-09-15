import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

import {
  approvePlan,
  executePlan,
  expectStage,
  resetToStart,
  runHeroScenario,
} from "./helpers";

/**
 * Accessibility, verified in a real renderer.
 *
 * `src/a11y.test.ts` (Vitest) checks the design *tokens* — that a reduced-motion
 * block exists, that focus styles are declared. That is a static guarantee, and
 * a static guarantee cannot tell you the rendered result is usable.
 *
 * This spec checks the rendered result: axe-core for WCAG rules including colour
 * contrast, plus keyboard and motion behaviour that only exists at runtime.
 */

/** Report violations with enough context to fix them without re-running. */
function describeViolations(
  violations: Awaited<ReturnType<AxeBuilder["analyze"]>>["violations"],
): string {
  return violations
    .map((violation) => {
      const targets = violation.nodes.map((node) => node.target.join(" ")).join(" | ");
      return `[${violation.impact ?? "unknown"}] ${violation.id}: ${violation.help}\n    at: ${targets}`;
    })
    .join("\n");
}

async function seriousViolations(page: Page) {
  const results = await new AxeBuilder({ page })
    // The command center is one long dashboard. The WCAG A/AA rule set is the
    // right bar; 'best-practice' adds opinionated noise the brief did not ask for.
    .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
    .analyze();
  return results.violations.filter((v) => v.impact === "serious" || v.impact === "critical");
}

test.describe("accessibility", () => {
  test("initial view has no serious WCAG violations", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByText("Load the hero scenario")).toBeVisible();

    expect(describeViolations(await seriousViolations(page))).toEqual("");
  });

  test("the populated dashboard has no serious WCAG violations", async ({ page }) => {
    await page.goto("/");
    await runHeroScenario(page);

    expect(describeViolations(await seriousViolations(page))).toEqual("");
  });

  test("the executed dashboard has no serious WCAG violations", async ({ page }) => {
    await page.goto("/");
    await runHeroScenario(page);
    await approvePlan(page);
    await executePlan(page);

    expect(describeViolations(await seriousViolations(page))).toEqual("");
  });

  test("skip link is the first stop and lands on the main landmark", async ({ page }) => {
    await page.goto("/");
    await page.keyboard.press("Tab");

    await expect(page.locator(":focus")).toHaveText(/Skip to main content/);
    await expect(page.locator("main#main")).toHaveCount(1);

    await page.keyboard.press("Enter");
    await expect(page).toHaveURL(/#main$/);
  });

  test("the hero scenario is reachable and runnable by keyboard alone", async ({ page }) => {
    await page.goto("/");
    // Start from a clean slate: the backend holds one mutable demo state, and a
    // leftover approval from an earlier spec would change what "run" does.
    await resetToStart(page);

    const runButton = page.getByRole("button", { name: "Run hero scenario" }).first();
    await runButton.focus();
    await expect(runButton).toBeFocused();
    await page.keyboard.press("Enter");

    await expectStage(page, "APPROVE", "awaiting");
  });

  test("status is never conveyed by colour alone", async ({ page }) => {
    await page.goto("/");
    await runHeroScenario(page);

    // Each stage pairs a glyph with a state word, and the word is exposed as
    // text — so a colour-blind viewer and a screen reader both get the status.
    const strip = page.getByTestId("loop-stage-POLICY");
    await expect(strip).toContainText(/Done/i);
    await expect(page.getByTestId("loop-stage-EXECUTE")).toContainText(/Pending/i);
    await expect(page.getByTestId("loop-stage-APPROVE")).toContainText(/Awaiting human/i);
  });

  test("notifications are announced, not just drawn", async ({ page }) => {
    await page.goto("/");
    await page.getByRole("button", { name: "Reset" }).click();

    const toast = page.getByRole("status").first();
    await expect(toast).toBeVisible();
    await expect(toast).toHaveAttribute("aria-live", "polite");
  });

  test("focus is visibly indicated", async ({ page }) => {
    await page.goto("/");

    const reset = page.getByRole("button", { name: "Reset" });
    await page.keyboard.press("Tab");
    await reset.focus();

    const outline = await reset.evaluate((element) => {
      const style = getComputedStyle(element);
      return { width: style.outlineWidth, style: style.outlineStyle };
    });

    // The element must actually be focused, or this passes vacuously.
    await expect(reset).toBeFocused();
    expect(outline.style).not.toEqual("none");
    expect(Number.parseFloat(outline.width)).toBeGreaterThan(0);
  });

  test("reduced-motion preference collapses the animation tokens", async ({ page }) => {
    await page.emulateMedia({ reducedMotion: "reduce" });
    await page.goto("/");

    const durations = await page.evaluate(() => {
      const style = getComputedStyle(document.documentElement);
      return ["--dur-instant", "--dur-fast", "--dur-normal", "--dur-slow"].map((token) =>
        style.getPropertyValue(token).trim(),
      );
    });

    expect(durations).toEqual(["1ms", "1ms", "1ms", "1ms"]);
  });

  test("every interactive control has an accessible name", async ({ page }) => {
    await page.goto("/");
    await runHeroScenario(page);

    const unnamed = await page.getByRole("button").evaluateAll((buttons) =>
      buttons
        .map((button) => ({
          label: button.getAttribute("aria-label"),
          text: button.textContent?.trim() ?? "",
          html: button.outerHTML.slice(0, 140),
        }))
        .filter((b) => !b.label && b.text.length === 0)
        .map((b) => b.html),
    );

    expect(unnamed).toEqual([]);
  });

  test("narrow viewports keep the journey operable", async ({ page }) => {
    await page.setViewportSize({ width: 1120, height: 800 });
    await page.goto("/");
    await resetToStart(page);

    // Below the rail breakpoint the side navigation is hidden, so the header
    // controls have to carry the entire presenter path on their own.
    await expect(page.getByRole("button", { name: "Reset" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Run hero scenario" }).first()).toBeVisible();

    await runHeroScenario(page);
    await expect(page.getByText("$3,908,933.39 · 10-day horizon")).toBeVisible();
  });
});
