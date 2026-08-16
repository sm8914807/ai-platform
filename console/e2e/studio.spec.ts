import { expect, test } from "@playwright/test";

test.describe("Platform Studio", () => {
  test.beforeEach(async ({ page }) => {
    await page.addInitScript(() => {
      localStorage.removeItem("platform.studio.token");
      localStorage.removeItem("platform.studio.user");
    });
  });

  test("login, overview health, and resources nav", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByTestId("login-screen")).toBeVisible();
    await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();

    await page.getByTestId("login-email").fill("e2e@example.com");
    await page.getByTestId("login-org").fill("default-org");
    await page.getByTestId("login-submit").click();

    await expect(page.getByTestId("studio-app")).toBeVisible({ timeout: 20_000 });
    await expect(page.getByTestId("user-email")).toContainText("e2e@example.com");
    await expect(page.getByTestId("health-version")).not.toHaveText("…", { timeout: 20_000 });
    await expect(page.getByTestId("ns-switcher")).toBeVisible();

    await page.getByTestId("nav-resources").click();
    await expect(page.getByRole("heading", { name: /resources/i })).toBeVisible();
    await expect(page.getByRole("button", { name: /new resource/i })).toBeVisible();

    await page.getByTestId("nav-collaboration").click();
    await expect(page.getByRole("heading", { name: /multi-agent/i })).toBeVisible();

    await page.getByTestId("nav-metrics").click();
    await expect(page.getByRole("heading", { name: /metrics/i })).toBeVisible();

    await page.getByTestId("nav-git").click();
    await expect(page.getByRole("heading", { name: /git sync/i })).toBeVisible();

    await page.getByTestId("nav-regions").click();
    await expect(page.getByRole("heading", { name: /regions & edge/i })).toBeVisible();

    await page.getByTestId("nav-hitl").click();
    await expect(page.getByRole("heading", { name: /hitl inbox/i })).toBeVisible();

    await page.getByTestId("sign-out").click();
    await expect(page.getByTestId("login-screen")).toBeVisible();
  });
});
