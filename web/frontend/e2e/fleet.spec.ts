import { resetFixture } from "./helpers";
import { expect, test } from "@playwright/test";
test.beforeEach(resetFixture);
import { boardCalls } from "./helpers";

test("the fleet renders the fixture session with its physical identity", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("button", { name: /^forge-e2e/ })).toBeVisible();
  await page.getByRole("button", { name: /^forge-e2e/ }).click();
  await expect(page.getByRole("heading", { name: /^forge-e2e.*\$/ })).toBeVisible();
  await expect(page.getByText("Session pending stage", { exact: true })).toBeVisible();
  await expect(page.getByText(/coding/).first()).toBeVisible();
});

test("two tabs share ONE board sampler", async ({ context }) => {
  const first = await context.newPage();
  await first.goto("/");
  await expect(first.getByRole("button", { name: /^forge-e2e/ })).toBeVisible();
  // Let the cooldown lapse at least once so a scan is definitely due.
  await first.waitForTimeout(6000);
  const before = boardCalls();

  const second = await context.newPage();
  await second.goto("/");
  await expect(second.getByRole("button", { name: /^forge-e2e/ })).toBeVisible();
  await second.waitForTimeout(6000);

  const after = boardCalls();
  // Two tabs polling at 2 s for 6 s would be six scans each if reads drove collection.
  // Single flight plus the cooldown floor caps it far below that.
  expect(after - before).toBeLessThanOrEqual(3);
});

test("the objective excerpt is labelled as a source excerpt, not a summary", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: /^forge-e2e/ }).click();
  await expect(page.getByText(/Source excerpt: \.dev\/proposals\/alpha/)).toBeVisible();
});
