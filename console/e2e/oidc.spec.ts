import { expect, test } from "@playwright/test";

/**
 * OIDC login UI without a real IdP.
 * Mocks /v1/auth/config (and start) so Studio shows enterprise mode.
 */
test.describe("OIDC login UI", () => {
  test.beforeEach(async ({ page }) => {
    await page.addInitScript(() => {
      localStorage.removeItem("platform.studio.token");
      localStorage.removeItem("platform.studio.user");
    });

    // Block the real IdP navigation so the app-origin context (and its
    // sessionStorage PKCE state) survives for assertions.
    await page.route(/login\.microsoftonline\.com/, async (route) => {
      await route.abort();
    });

    await page.route("**/v1/auth/config", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          mode: "oidc",
          devLoginEnabled: false,
          defaultOrgId: "default-org",
          oidc: {
            issuer: "https://login.microsoftonline.com/tenant/v2.0",
            clientId: "e2e-azure-app",
            redirectUri: "http://127.0.0.1:5173/",
            scopes: "openid profile email",
            audience: "",
            hasClientSecret: true,
          },
        }),
      });
    });

    await page.route("**/v1/auth/oidc/start", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          authorizationUrl:
            "https://login.microsoftonline.com/tenant/oauth2/v2.0/authorize?client_id=e2e",
          state: "e2e-state",
          nonce: "e2e-nonce",
        }),
      });
    });
  });

  test("shows IdP button and hides email login when OIDC-only", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByTestId("login-screen")).toBeVisible();
    await expect(page.getByTestId("oidc-login")).toBeVisible();
    await expect(page.getByTestId("oidc-login")).toContainText(/microsoft|oidc|sign in/i);
    await expect(page.getByTestId("login-email")).toHaveCount(0);
    await expect(page.getByTestId("login-submit")).toHaveCount(0);
  });

  test("OIDC button calls start and stores PKCE pending session", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByTestId("oidc-login")).toBeVisible();

    const startReq = page.waitForRequest("**/v1/auth/oidc/start");
    await page.getByTestId("oidc-login").click();
    const req = await startReq;
    expect(req.method()).toBe("POST");

    // PKCE session is persisted before the (aborted) IdP redirect.
    await expect
      .poll(
        async () => page.evaluate(() => sessionStorage.getItem("platform.oidc.pending")),
        { timeout: 10_000 },
      )
      .not.toBeNull();
    const pending = await page.evaluate(() =>
      sessionStorage.getItem("platform.oidc.pending"),
    );
    const parsed = JSON.parse(String(pending));
    expect(parsed.state).toBe("e2e-state");
    expect(parsed.verifier).toBeTruthy();
  });
});
