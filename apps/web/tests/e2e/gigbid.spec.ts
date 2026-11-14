import { expect, test } from "@playwright/test";
import { mockApi } from "./fixtures";

test("onboarding: create account with persona, rates and Connects budget", async ({ page }) => {
  await mockApi(page);
  await page.goto("/login");
  await page.getByRole("tab", { name: "Create account" }).click();
  await page.getByTestId("email").fill("new@freelancer.dev");
  await page.getByTestId("password").fill("longpassword");
  await page.getByTestId("persona").selectOption("data_analyst");
  await page.getByTestId("submit").click();
  await expect(page).toHaveURL(/settings/);
  await expect(page.getByTestId("memory")).toBeVisible();
});

test("approving a proposal sends the approve decision", async ({ page }) => {
  const calls = await mockApi(page);
  await page.goto("/approvals");
  const panel = page.getByTestId("approval-panel");
  await expect(panel).toContainText("8 Connects");
  await expect(page.getByTestId("claim").first()).toBeVisible();
  await page.getByTestId("approve").click();
  await expect.poll(() => calls.approvals.length).toBe(1);
  expect(calls.approvals[0].action).toBe("approve");
});

test("rejecting requires a reason", async ({ page }) => {
  const calls = await mockApi(page);
  await page.goto("/approvals");
  await page.getByTestId("reject").click();
  await expect(page.getByTestId("confirm-reject")).toBeDisabled();
  await page.getByTestId("reject-reason").fill("client asks for free samples");
  await page.getByTestId("confirm-reject").click();
  await expect.poll(() => calls.approvals.length).toBe(1);
  expect(calls.approvals[0]).toMatchObject({ action: "reject", reason: "client asks for free samples" });
});

test("editing a draft sends the edited text for re-check", async ({ page }) => {
  const calls = await mockApi(page);
  await page.goto("/approvals");
  await page.getByTestId("edit").click();
  await page.getByTestId("edit-box").fill("You need grounded answers. I built a RAG assistant for a legal client.");
  await page.getByTestId("save-edit").click();
  await expect.poll(() => calls.approvals.length).toBe(1);
  expect(calls.approvals[0].action).toBe("edit");
  expect(calls.approvals[0].edited_text).toContain("legal client");
});

test("degraded mode: banner when the marketplace is down", async ({ page }) => {
  await mockApi(page, { marketplaceDown: true });
  await page.goto("/inbox");
  await expect(page.getByTestId("degraded-banner")).toContainText("Marketplace unavailable");
  await expect(page.getByTestId("inbox-table")).toBeVisible();
});

test("evidence appears on keyboard focus of a claim", async ({ page }) => {
  await mockApi(page);
  await page.goto("/approvals");
  await page.getByTestId("claim").first().focus();
  await expect(page.getByRole("tooltip").first()).toContainText("case_proj_1");
});

test("removing evidence then regenerating drops the claim that relied on it", async ({ page }) => {
  const calls = await mockApi(page);
  await page.goto("/approvals");
  await page.getByTestId("evidence-list").locator("summary").click();
  await page.getByRole("button", { name: "Remove case_proj_1 from portfolio" }).click();
  await expect.poll(() => calls.removed).toEqual(["case_proj_1"]);
  await page.getByRole("button", { name: "Regenerate paragraph 1" }).click();
  await expect(page.getByTestId("regen-note")).toContainText("removed 1 unsupported sentence");
  await expect(page.getByTestId("edit-box")).not.toHaveValue(/300 staff questions/);
});
