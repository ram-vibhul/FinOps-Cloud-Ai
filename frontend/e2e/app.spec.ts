import { expect, type Page, test } from "@playwright/test";

/** The paths a new customer takes first (11.9). Each test signs in as a fresh person. */
async function signIn(page: Page, email: string) {
  await page.goto("/sign-in");
  await page.getByPlaceholder("you@example.com").fill(email);
  await page.getByRole("button", { name: "Continue" }).click();
  await expect(page).toHaveURL(/\/welcome$/);
}

test("sign in and explore the demo", async ({ page }) => {
  await signIn(page, "demo-viewer@example.test");
  await page.getByRole("button", { name: "Explore the demo" }).click();
  await expect(page.getByText("You are exploring the demo organisation")).toBeVisible();
  await page.getByRole("link", { name: "Costs", exact: true }).first().click();
  await expect(page.getByRole("heading", { name: "Costs" })).toBeVisible();
  await expect(page.getByRole("figure").first()).toBeVisible(); // a chart rendered
});

test("radar shows priced changes with honest ages", async ({ page }) => {
  await signIn(page, "radar-viewer@example.test");
  await page.getByRole("button", { name: "Explore the demo" }).click();
  await page.getByRole("link", { name: "Radar" }).first().click();
  await expect(page.getByRole("heading", { name: "Radar" })).toBeVisible();
  await expect(page.getByRole("row").filter({ hasText: "4 x m5.4xlarge" })).toBeVisible();
  await expect(page.getByText("Not in the bill yet").first()).toBeVisible();
  // The newest demo change is at most 2 hours old. A time-zone bug would show
  // "7 h ago" or similar in this India-time browser (it did before 2026-11-12).
  const firstAge = await page.getByRole("row").nth(1).getByRole("cell").first().innerText();
  expect(firstAge).toMatch(/^(just now|\d+ min ago|[0-2] h ago)$/);
});

test("create an organisation and start connecting AWS", async ({ page }) => {
  await signIn(page, "founder@example.test");
  await page.getByLabel("Organisation name").fill("E2E Ltd");
  await page.getByRole("button", { name: "Create organisation" }).click();
  await page.goto("/settings?tab=clouds");
  await page.getByRole("button", { name: /Connect AWS/ }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toBeVisible();
  await dialog.getByLabel("Name").fill("Production");
  await dialog.getByRole("button", { name: /Continue|Create|Next/ }).first().click();
  // The read-only role setup: a CloudFormation template with this connection's external id.
  await expect(page.getByText(/CloudFormation/).first()).toBeVisible();
  await expect(page.getByText(/ccradar-/).first()).toBeVisible();
});
