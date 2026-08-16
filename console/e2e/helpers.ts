import { expect, type APIRequestContext, type Page } from "@playwright/test";

export const NS = "default-org/default-project";
export const API = process.env.E2E_API_BASE ?? "http://127.0.0.1:8080";

export async function studioLogin(
  page: Page,
  email = "e2e@example.com",
  orgId = "default-org",
): Promise<string> {
  await page.goto("/");
  await expect(page.getByTestId("login-screen")).toBeVisible();
  await page.getByTestId("login-email").fill(email);
  await page.getByTestId("login-org").fill(orgId);
  await page.getByTestId("login-submit").click();
  await expect(page.getByTestId("studio-app")).toBeVisible({ timeout: 20_000 });
  const token = await page.evaluate(() => localStorage.getItem("platform.studio.token"));
  if (!token) throw new Error("missing studio token after login");
  return token;
}

export async function apiJson(
  request: APIRequestContext,
  method: string,
  path: string,
  token: string,
  body?: unknown,
) {
  const res = await request.fetch(`${API}${path}`, {
    method,
    headers: {
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json",
    },
    data: body === undefined ? undefined : JSON.stringify(body),
  });
  const text = await res.text();
  let json: unknown = null;
  try {
    json = text ? JSON.parse(text) : null;
  } catch {
    json = text;
  }
  if (!res.ok()) {
    throw new Error(`${method} ${path} -> ${res.status()}: ${text}`);
  }
  return json as Record<string, unknown>;
}

export async function upsertAndPublish(
  request: APIRequestContext,
  token: string,
  kind: string,
  name: string,
  spec: Record<string, unknown>,
  version = "1.0.0",
) {
  await apiJson(request, "PUT", `/v1/${NS}/${kind}/${name}/versions/${version}`, token, {
    api_version: "platform.ai/v1",
    kind,
    metadata: { name, version, namespace: NS },
    spec,
  });
  await apiJson(request, "POST", `/v1/${NS}/${kind}/${name}/publish`, token, {
    version,
    principal: "e2e",
  });
}

/** Publish a HITL-first workflow plus mock agent/prompt/model deps. */
export async function seedHitlWorkflow(request: APIRequestContext, token: string, suffix: string) {
  const model = `e2e-model-${suffix}`;
  const prompt = `e2e-prompt-${suffix}`;
  const agent = `e2e-agent-${suffix}`;
  const workflow = `e2e-hitl-${suffix}`;

  await upsertAndPublish(request, token, "ModelRoute", model, {
    strategy: "weightedFallback",
    candidates: [{ provider: "mock", model: "mock-1", weight: 100, fallback: true }],
  });
  await upsertAndPublish(request, token, "Prompt", prompt, {
    template: "E2E task: {{ message }}",
  });
  await upsertAndPublish(request, token, "Agent", agent, {
    role: "executor",
    modelRef: `models/${model}`,
    promptRef: `prompts/${prompt}`,
  });
  await upsertAndPublish(request, token, "Workflow", workflow, {
    steps: [
      {
        id: "approve",
        type: "humanApproval",
        ref: "approval-flows/default",
      },
      {
        id: "done",
        type: "agent",
        ref: `agents/${agent}`,
        when: "$.steps.approve.status == approved",
      },
    ],
  });

  const started = await apiJson(request, "POST", `/v1/${NS}/execute`, token, {
    resource_ref: `workflows/${workflow}`,
    input: { message: "e2e hitl" },
  });
  return { workflow, runId: String(started.run_id ?? started.runId), started };
}
