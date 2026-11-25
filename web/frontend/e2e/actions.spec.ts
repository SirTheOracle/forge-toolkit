import { resetFixture } from "./helpers";
import { expect, test } from "@playwright/test";
test.beforeEach(resetFixture);
import { dispatchCalls } from "./helpers";

test("a browser message reaches pane 0 with seat, pointer and the expectation tuple",
  async ({ page }) => {
    await page.goto("/");
    await page.getByRole("button", { name: /^forge-e2e/ }).click();
    const before = dispatchCalls().length;
    await page.getByLabel("message").fill("hello from the browser");
    await page.getByRole("button", { name: "Send" }).click();
    await expect(page.getByText(/Delivered to pane 0/)).toBeVisible();

    const calls = dispatchCalls();
    expect(calls.length).toBe(before + 1);
    const argv = calls[calls.length - 1];
    expect(argv).toContain("@forge-e2e");
    expect(argv).toContain("--sender");
    expect(argv).toContain("seat");
    expect(argv).toContain("--pointer");
    expect(argv).toContain("--expected-session-id");
    expect(argv[argv.indexOf("--expected-session-id") + 1]).toMatch(/^\$\d+$/);
    expect(argv[argv.length - 2]).toBe("--");
    expect(argv[argv.length - 1]).toBe("hello from the browser");
  });

test("a double click does not dispatch twice", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: /^forge-e2e/ }).click();
  const before = dispatchCalls().length;
  await page.getByLabel("message").fill("only once");
  const send = page.getByRole("button", { name: "Send" });
  await send.click();
  await send.click({ force: true });
  await page.waitForTimeout(1500);
  const sent = dispatchCalls().slice(before).filter((argv) => argv.includes("only once"));
  expect(sent.length).toBe(1);
});

test("the draft survives a poll and is not cleared by a send", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: /^forge-e2e/ }).click();
  await page.getByLabel("message").fill("a half-typed thought");
  await page.waitForTimeout(5000);                    // several 2 s polls
  await expect(page.getByLabel("message")).toHaveValue("a half-typed thought");
});

test("the selected session survives a reload", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: /^forge-e2e/ }).click();
  await expect(page.getByText(/Stage facts/)).toBeVisible();
  await page.reload();
  await expect(page.getByText(/Stage facts/)).toBeVisible();
});