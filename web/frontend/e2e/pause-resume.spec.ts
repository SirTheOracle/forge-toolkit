import { resetFixture } from "./helpers";
import { expect, test } from "@playwright/test";
test.beforeEach(resetFixture);
import { dispatchCalls } from "./helpers";

test("pause sends the fixed literal phrase with no pointer, and says REQUESTED",
  async ({ page }) => {
    await page.goto("/");
    await page.getByRole("button", { name: /^forge-e2e/ }).click();
    const before = dispatchCalls().length;
    await page.getByRole("button", { name: "Pause" }).click();
    await expect(page.getByText(/Pause REQUESTED/)).toBeVisible();

    const argv = dispatchCalls()[before];
    expect(argv[argv.length - 1]).toBe("forge-pause");
    expect(argv).not.toContain("--pointer");
  });

test("resume goes through the composer; there is no Resume or Skip control anywhere",
  async ({ page }) => {
    await page.goto("/");
    await page.getByRole("button", { name: /^forge-e2e/ }).click();
    await expect(page.getByRole("button", { name: /^Resume$/ })).toHaveCount(0);
    await expect(page.getByRole("button", { name: /Skip/i })).toHaveCount(0);

    const before = dispatchCalls().length;
    await page.getByRole("button", { name: /Insert forge-resume/ }).click();
    await expect(page.getByLabel("message")).toHaveValue("forge-resume");
    await page.getByRole("button", { name: "Send" }).click();
    await expect(page.getByText(/Delivered to pane 0/)).toBeVisible();

    const argv = dispatchCalls()[before];
    expect(argv[argv.length - 1]).toBe("forge-resume");
  });

test("stop is a request and says so", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: /^forge-e2e/ }).click();
  await expect(page.getByText(/does not kill tmux or any running worker/)).toBeVisible();
  await page.getByRole("button", { name: "Stop" }).click();
  await expect(page.getByText(/Stop REQUESTED/)).toBeVisible();
});