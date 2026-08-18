import { expect, test } from "@playwright/test";
import { apiJson, seedHitlWorkflow, studioLogin } from "./helpers";

test.describe("Platform Studio", () => {
  test.beforeEach(async ({ page }) => {
    await page.addInitScript(() => {
      localStorage.removeItem("platform.studio.token");
      localStorage.removeItem("platform.studio.user");
    });
  });

  test("login, overview health, and resources nav", async ({ page }) => {
    await studioLogin(page);
    await expect(page.getByTestId("user-email")).toContainText("e2e@example.com");
    await expect(page.getByTestId("health-version")).not.toHaveText("…", { timeout: 20_000 });
    await expect(page.getByTestId("ns-switcher")).toBeVisible();

    await page.getByTestId("nav-resources").click();
    await expect(page.getByRole("heading", { name: /resources/i })).toBeVisible();
    await expect(page.getByRole("button", { name: /new resource/i })).toBeVisible();

    await page.getByTestId("nav-collaboration").click();
    await expect(page.getByRole("heading", { name: /multi-agent/i })).toBeVisible();

    await page.getByTestId("nav-readiness").click();
    await expect(page.getByTestId("readiness-view")).toBeVisible();
    await expect(page.getByRole("heading", { name: /production readiness/i })).toBeVisible();

    await page.getByTestId("nav-metrics").click();
    await expect(page.getByRole("heading", { name: /metrics/i })).toBeVisible();

    await page.getByTestId("nav-git").click();
    await expect(page.getByRole("heading", { name: /git sync/i })).toBeVisible();

    await page.getByTestId("nav-regions").click();
    await expect(page.getByRole("heading", { name: /regions & edge/i })).toBeVisible();

    await page.getByTestId("nav-hitl").click();
    await expect(page.getByRole("heading", { name: /hitl inbox/i })).toBeVisible();

    await page.getByTestId("nav-identity").click();
    await expect(page.getByTestId("identity-view")).toBeVisible();

    await page.getByTestId("nav-activity").click();
    await expect(page.getByRole("heading", { name: /activity/i })).toBeVisible();

    await page.getByTestId("sign-out").click();
    await expect(page.getByTestId("login-screen")).toBeVisible();
  });

  test("SCIM identity: create and deactivate user", async ({ page }) => {
    await studioLogin(page, "scim-admin@example.com");
    await page.getByTestId("nav-identity").click();
    await expect(page.getByTestId("identity-view")).toBeVisible();

    const email = `scim-e2e-${Date.now()}@example.com`;
    await page.getByTestId("identity-email").fill(email);
    await page.getByTestId("identity-display-name").fill("SCIM E2E");
    await page.getByTestId("identity-create").click();

    await expect(page.getByText(email).first()).toBeVisible({ timeout: 15_000 });
    const row = page.locator(".list-item", { hasText: email });
    await expect(row.getByText("active")).toBeVisible();
    await row.getByRole("button", { name: /deactivate/i }).click();
    await expect(row.getByText("inactive")).toBeVisible({ timeout: 15_000 });
  });

  test("HITL inbox: approve and resume paused workflow", async ({ page, request }) => {
    const token = await studioLogin(page, "hitl-e2e@example.com");
    const suffix = String(Date.now()).slice(-8);
    const { runId, started } = await seedHitlWorkflow(request, token, suffix);
    expect(started.status).toBe("waiting_approval");
    expect(runId).toBeTruthy();

    await page.getByTestId("nav-hitl").click();
    await expect(page.getByRole("heading", { name: /hitl inbox/i })).toBeVisible();
    await page.getByRole("button", { name: /refresh/i }).click();

    const item = page.getByTestId(`hitl-run-${runId}`);
    await expect(item).toBeVisible({ timeout: 15_000 });
    await item.click();
    // Wait for the run detail to load before approving (openRun is async and
    // would otherwise race the approve/resume result into the detail pane).
    await expect(page.locator(".code")).toContainText(runId, { timeout: 15_000 });

    await page.getByTestId("hitl-approve").click();

    // The UI approve triggers approve + resume; confirm the run reaches a
    // terminal state via the API (deterministic, no detail-pane timing race).
    await expect
      .poll(
        async () => {
          const run = await apiJson(
            request,
            "GET",
            `/v1/workflows/runs/${encodeURIComponent(runId)}`,
            token,
          );
          return String(run.status ?? "");
        },
        { timeout: 20_000 },
      )
      .toMatch(/completed/i);

    // The paused run also drops out of the waiting-approval inbox.
    await expect(page.getByTestId(`hitl-run-${runId}`)).toHaveCount(0, { timeout: 15_000 });
  });
});
