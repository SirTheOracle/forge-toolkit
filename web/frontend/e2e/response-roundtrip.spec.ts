import { resetFixture } from "./helpers";
import { expect, test } from "@playwright/test";
test.beforeEach(resetFixture);

test("message -> response.<id>.txt -> the reply appears and survives a reload",
  async ({ page }) => {
    await page.goto("/");
    await page.getByRole("button", { name: /^forge-e2e/ }).click();
    await page.getByLabel("message").fill("what is the status?");
    await page.getByRole("button", { name: "Send" }).click();
    await expect(page.getByText(/Delivered to pane 0/)).toBeVisible();

    // The stub wrote .dev/attention/payloads/response.cc-e2e-0001.txt and a matching
    // stop.forge-e2e.json; the collector reconstructs the association from those records.
    const reply = page.locator("pre").filter({ hasText: /^reply: (what is the status\?|trigger a reply)$/ });
    await expect(reply).toBeVisible({ timeout: 20_000 });
    await expect(page.getByText(/Response to cc-/)).toBeVisible();

    // AFTER RELOAD the association must be rebuilt from the attention records, not from an
    // in-memory receipt.
    await page.reload();
    await page.getByRole("button", { name: /^forge-e2e/ }).click();
    await expect(reply).toBeVisible({ timeout: 20_000 });
  });

test("writer truncation is reported from the record, with its basis labelled",
  async ({ page }) => {
    await page.goto("/");
    await page.getByRole("button", { name: /^forge-e2e/ }).click();
    await page.getByLabel("message").fill("trigger a reply");
    await page.getByRole("button", { name: "Send" }).click();
    await expect(page.locator("pre").filter({ hasText: /^reply: (what is the status\?|trigger a reply)$/ })).toBeVisible({ timeout: 20_000 });
    await expect(page.getByText(/Writer truncation: no/).first()).toBeVisible();
    await expect(page.getByText(/BEFORE redaction/).first()).toBeVisible();
  });
