const API_BASE = import.meta.env.VITE_API_BASE ?? "";
const TOKEN_KEY = "platform.studio.token";
const USER_KEY = "platform.studio.user";
const NS_KEY = "platform.studio.namespace";
const NS_RECENT_KEY = "platform.studio.namespaces";

export const DEFAULT_NS = "default-org/default-project";

export type AuthUser = { id: string; email: string };

export type NamespaceInfo = { id: string; path: string; env: string };

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}

export function getUser(): AuthUser | null {
  const raw = localStorage.getItem(USER_KEY);
  if (!raw) return null;
  try {
    return JSON.parse(raw) as AuthUser;
  } catch {
    return null;
  }
}

export function getNamespace(): string {
  return localStorage.getItem(NS_KEY) || DEFAULT_NS;
}

export function setNamespace(path: string) {
  const clean = path.trim().replace(/^\/+|\/+$/g, "");
  localStorage.setItem(NS_KEY, clean);
  const recent = getRecentNamespaces().filter((p) => p !== clean);
  recent.unshift(clean);
  localStorage.setItem(NS_RECENT_KEY, JSON.stringify(recent.slice(0, 12)));
}

export function getRecentNamespaces(): string[] {
  try {
    const raw = localStorage.getItem(NS_RECENT_KEY);
    if (!raw) return [DEFAULT_NS];
    const parsed = JSON.parse(raw) as string[];
    return Array.isArray(parsed) && parsed.length ? parsed : [DEFAULT_NS];
  } catch {
    return [DEFAULT_NS];
  }
}

export function setSession(token: string, user: AuthUser) {
  localStorage.setItem(TOKEN_KEY, token);
  localStorage.setItem(USER_KEY, JSON.stringify(user));
}

export function clearSession() {
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(USER_KEY);
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(init?.headers as Record<string, string> | undefined),
  };
  const token = getToken();
  if (token) headers.Authorization = `Bearer ${token}`;

  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers,
  });
  if (res.status === 401) {
    clearSession();
    throw new Error("Session expired — sign in again");
  }
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || res.statusText);
  }
  return res.json() as Promise<T>;
}

export type Resource = {
  kind: string;
  name: string;
  version: string;
  spec: Record<string, unknown>;
};

export type Trace = {
  id: string;
  agent_ref: string;
  trace_type: string;
  tags: string[];
  payload: Record<string, unknown>;
  outcome?: string;
  created_at: string;
};

export type DiscoveredAgent = {
  id: string;
  agent_ref: string;
  address?: string;
  capabilities: string[];
  status: string;
};

export type ExecutionEvent = {
  type: "token" | "tool_call" | "tool_result" | "approval_required" | "done" | "error";
  data: Record<string, unknown>;
  execution_id?: string | null;
};

export type CompliancePack = {
  id: string;
  name: string;
  framework: string;
  version: string;
  description: string;
};

export type PlatformMessage = {
  id: string;
  sender: string;
  recipient: string;
  subject?: string;
  payload: Record<string, unknown>;
  status: string;
};

export type SecretMeta = {
  id: string;
  name: string;
  created_at: string;
  rotated_at?: string | null;
};

export type FederatedPeer = {
  domain: string;
  gateway: string;
  version: string;
  features: string[];
};

export type Health = {
  status: string;
  version: string;
  registryBackend?: string;
  sqlBackend?: string;
  federationDomain?: string;
};

export type MarketplacePlugin = {
  id?: string;
  name: string;
  version?: string;
  tier?: string;
  description?: string;
  manifest?: Record<string, unknown>;
};

export type RegionInfo = {
  id: string;
  name: string;
  endpoint: string;
  data_residency?: string | null;
  is_primary: boolean;
  status: string;
};

export type EdgeNode = {
  id: string;
  namespaceId: string;
  regionId?: string | null;
  regionName?: string | null;
  nodeType: string;
  bundleHash?: string | null;
  bundleCachePath?: string | null;
  lastSyncAt?: string | null;
  lastTelemetryAt?: string | null;
  status: string;
  metadata: Record<string, unknown>;
  createdAt?: string | null;
};

export type HitlInboxItem = {
  runId: string;
  workflowRef?: string | null;
  status: string;
  currentStepId?: string | null;
  startedAt?: string;
  input: Record<string, unknown>;
  steps: Record<string, unknown>;
  pendingApproval?: {
    step_id?: string;
    approval_ref?: string;
    reason?: string;
    decision?: string;
  } | null;
};

export type MetricStats = {
  requests: number;
  successes: number;
  failures: number;
  successRate: number;
  avgLatencyMs: number;
  p50LatencyMs: number;
  p95LatencyMs: number;
  totalCostUnits: number;
};

export const api = {
  health: () => request<Health>("/health"),
  authConfig: () =>
    request<{
      mode: "dev" | "oidc";
      devLoginEnabled: boolean;
      defaultOrgId: string;
      oidc?: {
        issuer: string;
        clientId: string;
        redirectUri: string;
        scopes: string;
        audience: string;
        hasClientSecret: boolean;
      };
    }>("/v1/auth/config"),
  login: (email: string, orgId = "default-org", displayName?: string) =>
    request<{
      accessToken: string;
      tokenType: string;
      expiresIn: number;
      user: AuthUser;
      provider?: string;
    }>("/v1/auth/login", {
      method: "POST",
      body: JSON.stringify({ email, orgId, displayName }),
    }),
  oidcStart: (body: { codeChallenge: string; orgId?: string; redirectUri?: string }) =>
    request<{ authorizationUrl: string; state: string; nonce: string }>(
      "/v1/auth/oidc/start",
      {
        method: "POST",
        body: JSON.stringify(body),
      },
    ),
  oidcCallback: (body: {
    code: string;
    state: string;
    codeVerifier: string;
    orgId?: string;
  }) =>
    request<{
      accessToken: string;
      tokenType: string;
      expiresIn: number;
      user: AuthUser;
      provider?: string;
    }>("/v1/auth/oidc/callback", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  listResources: (ns = DEFAULT_NS) =>
    request<{ resources: Resource[] }>(`/v1/${ns}/resources`),
  upsertResource: (
    ns: string,
    kind: string,
    name: string,
    version: string,
    spec: Record<string, unknown>,
  ) =>
    request<{ id: string; version: string }>(
      `/v1/${ns}/${kind}/${name}/versions/${version}`,
      {
        method: "PUT",
        body: JSON.stringify({
          api_version: "platform.ai/v1",
          kind,
          metadata: { name, version, namespace: ns },
          spec,
        }),
      },
    ),
  publishResource: (
    ns: string,
    kind: string,
    name: string,
    version: string,
    evalSuiteRef?: string,
  ) =>
    request<Record<string, unknown>>(`/v1/${ns}/${kind}/${name}/publish`, {
      method: "POST",
      body: JSON.stringify({
        version,
        principal: "console",
        ...(evalSuiteRef ? { evalSuiteRef } : {}),
      }),
    }),
  runEvaluation: (
    ns: string,
    body: { suiteRef: string; targetRef: string; targetVersion?: string; suiteVersion?: string },
  ) =>
    request<{
      runId: string;
      passed: boolean;
      scores: Record<string, number>;
      overall: number;
      gateReason?: string | null;
      details: unknown[];
    }>(`/v1/${ns}/evaluations/run`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  recentEvaluations: (ns = DEFAULT_NS) =>
    request<{ runs: Record<string, unknown>[] }>(`/v1/${ns}/evaluations/recent`),
  listTraces: (ns = DEFAULT_NS) =>
    request<{ traces: Trace[] }>(`/v1/${ns}/traces`),
  createTrace: (ns: string, body: unknown) =>
    request<Trace>(`/v1/${ns}/traces`, { method: "POST", body: JSON.stringify(body) }),
  queryPrecedents: (ns: string, body: unknown) =>
    request<{ precedents: Trace[] }>(`/v1/${ns}/traces/precedents`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  listAgents: (ns = DEFAULT_NS) =>
    request<{ agents: DiscoveredAgent[] }>(`/v1/${ns}/discovery/agents`),
  syncDiscovery: (ns = DEFAULT_NS) =>
    request<{ synced: number }>(`/v1/${ns}/discovery/sync`, { method: "POST" }),
  discover: (ns: string, capabilities: string[]) =>
    request<{ agents: DiscoveredAgent[] }>(`/v1/${ns}/discovery/find`, {
      method: "POST",
      body: JSON.stringify({ capabilities }),
    }),
  registerCapability: (
    ns: string,
    body: {
      agent_ref: string;
      address?: string;
      capabilities: string[];
      schemas?: string[];
      delivery_mode?: string;
    },
  ) =>
    request<DiscoveredAgent>(`/v1/${ns}/discovery/register`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  runResource: (
    ns: string,
    resourceRef: string,
    input: Record<string, unknown>,
    multiAgent = false,
  ) =>
    request<ExecutionEvent>(`/v1/${ns}/execute`, {
      method: "POST",
      body: JSON.stringify({ resource_ref: resourceRef, input, multiAgent }),
    }),
  listNamespaces: () =>
    request<{ namespaces: NamespaceInfo[]; default: string; environment: string }>(
      "/v1/namespaces",
    ),
  ensureNamespace: (path: string, environment?: string) =>
    request<NamespaceInfo>("/v1/namespaces", {
      method: "POST",
      body: JSON.stringify({ path, environment }),
    }),
  unpublishResource: (ns: string, kind: string, name: string) =>
    request<{ unpublished: boolean }>(`/v1/${ns}/${kind}/${name}/unpublish`, {
      method: "POST",
      body: JSON.stringify({}),
    }),
  planWorkflow: (ns: string, goal: string, plannerMode: "auto" | "llm" | "heuristic" = "auto") =>
    request<{
      workflow_id: string;
      status: string;
      ir: {
        name: string;
        description?: string;
        plannerBackend?: string;
        planner_backend?: string;
        steps: Array<{
          id: string;
          type: string;
          ref?: string;
          when?: string | null;
          description?: string;
        }>;
      };
      steps_output?: Record<string, unknown>;
      output: Record<string, unknown>;
    }>(`/v1/${ns}/workflows/plan`, {
      method: "POST",
      body: JSON.stringify({ goal, plannerMode }),
    }),
  approveWorkflow: (runId: string, decision = "approved") =>
    request<Record<string, unknown>>(`/v1/workflows/runs/${encodeURIComponent(runId)}/approve`, {
      method: "POST",
      body: JSON.stringify({ decision }),
    }),
  resumeWorkflow: (runId: string, ns = DEFAULT_NS) =>
    request<Record<string, unknown>>(
      `/v1/workflows/runs/${encodeURIComponent(runId)}/resume?namespace=${encodeURIComponent(ns)}`,
      { method: "POST" },
    ),
  promote: (ns: string, fromEnv: string, toEnv: string, requestedBy: string) =>
    request<{
      promotionId: string;
      status: string;
      resourcesPromoted?: number;
    }>(`/v1/${ns}/promote`, {
      method: "POST",
      body: JSON.stringify({ fromEnv, toEnv, requestedBy }),
    }),
  approvePromotion: (promoId: string, approvedBy: string) =>
    request<{
      promotionId: string;
      status: string;
      resourcesPromoted?: number;
    }>(`/v1/promotions/${encodeURIComponent(promoId)}/approve`, {
      method: "POST",
      body: JSON.stringify({ approvedBy }),
    }),
  listCompliance: () =>
    request<{ packs: CompliancePack[] }>("/v1/compliance/packs"),
  installCompliance: (ns: string, packId: string, installedBy?: string) =>
    request<Record<string, unknown>>(`/v1/${ns}/compliance/install`, {
      method: "POST",
      body: JSON.stringify({ packId, installedBy }),
    }),
  listMarketplace: (tier?: string) =>
    request<{ plugins: MarketplacePlugin[] }>(
      `/v1/marketplace/plugins${tier ? `?tier=${encodeURIComponent(tier)}` : ""}`,
    ),
  installMarketplace: (ns: string, pluginName: string, version?: string) =>
    request<Record<string, unknown>>(`/v1/${ns}/marketplace/install`, {
      method: "POST",
      body: JSON.stringify({ pluginName, version }),
    }),
  listRegions: () =>
    request<{ regions: RegionInfo[] }>("/v1/regions"),
  registerRegion: (body: {
    name: string;
    endpoint: string;
    dataResidency?: string;
    isPrimary?: boolean;
  }) =>
    request<{ regionId: string; name: string }>("/v1/regions", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  failoverRegion: (name: string) =>
    request<{ failed: string; newPrimary: RegionInfo }>(
      `/v1/regions/${encodeURIComponent(name)}/failover`,
      { method: "POST" },
    ),
  setPrimaryRegion: (name: string) =>
    request<{ primary: RegionInfo | null }>(
      `/v1/regions/${encodeURIComponent(name)}/primary`,
      { method: "POST" },
    ),
  listEdgeNodes: (limit = 100) =>
    request<{ nodes: EdgeNode[]; count: number }>(`/v1/edge/nodes?limit=${limit}`),
  registerEdgeNode: (body: {
    namespace: string;
    environment?: string;
    region?: string;
    bundleCachePath?: string;
  }) =>
    request<{ nodeId: string; namespaceId: string; mode: string }>("/v1/edge/register", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  listHitlInbox: (ns?: string, limit = 50) => {
    const q = new URLSearchParams({ limit: String(limit) });
    if (ns) q.set("namespace", ns);
    return request<{ items: HitlInboxItem[]; count: number }>(
      `/v1/workflows/inbox?${q.toString()}`,
    );
  },
  getWorkflowRun: (runId: string) =>
    request<HitlInboxItem & { output?: Record<string, unknown>; checkpointSeq?: number }>(
      `/v1/workflows/runs/${encodeURIComponent(runId)}`,
    ),
  listMessages: (ns = DEFAULT_NS, agent?: string) =>
    request<{ messages: PlatformMessage[] }>(
      `/v1/${ns}/messages${agent ? `?agent=${encodeURIComponent(agent)}` : ""}`,
    ),
  sendMessage: (ns: string, body: unknown) =>
    request<PlatformMessage>(`/v1/${ns}/messages`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  listSecrets: (ns = DEFAULT_NS) =>
    request<{ secrets: SecretMeta[] }>(`/v1/${ns}/secrets`),
  putSecret: (ns: string, name: string, value: string) =>
    request<SecretMeta>(`/v1/${ns}/secrets/${encodeURIComponent(name)}`, {
      method: "PUT",
      body: JSON.stringify({ value }),
    }),
  deleteSecret: (ns: string, name: string) =>
    request<{ deleted: boolean }>(`/v1/${ns}/secrets/${encodeURIComponent(name)}`, {
      method: "DELETE",
    }),
  federationInfo: () =>
    request<{ domain: string; features: string[]; peers: FederatedPeer[] }>(
      "/v1/federation/info",
    ),
  listPeers: () => request<{ peers: FederatedPeer[] }>("/v1/federation/peers"),
  registerPeer: (body: { domain: string; gateway: string; apiKey?: string }) =>
    request<FederatedPeer>("/v1/federation/peers", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  federationSend: (ns: string, body: unknown) =>
    request<Record<string, unknown>>(`/v1/${ns}/federation/send`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  amtpCapabilities: () => request<Record<string, unknown>>("/v1/capabilities"),
  amtpDnsTxt: (gateway: string) =>
    request<{ name: string; value: string }>(
      `/v1/amtp/dns-txt?gateway=${encodeURIComponent(gateway)}`,
    ),
  amtpSend: (body: unknown) =>
    request<Record<string, unknown>>("/v1/messages", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  amtpAgents: () =>
    request<{ agents: Array<Record<string, unknown>>; domain: string }>(
      "/v1/discovery/agents",
    ),
  amtpSchemas: () =>
    request<{ schemas: Array<Record<string, unknown>> }>("/v1/admin/schemas"),
  mcpList: (ns: string, body: { toolRef?: string; config?: Record<string, unknown> }) =>
    request<{
      server?: string;
      serverInfo?: Record<string, unknown>;
      tools: Array<{ name: string; description?: string; inputSchema?: Record<string, unknown> }>;
    }>(`/v1/${ns}/mcp/list`, { method: "POST", body: JSON.stringify(body) }),
  mcpCall: (
    ns: string,
    body: {
      toolRef?: string;
      config?: Record<string, unknown>;
      toolName?: string;
      arguments?: Record<string, unknown>;
    },
  ) =>
    request<{ result: Record<string, unknown>; latencyMs: number }>(`/v1/${ns}/mcp/call`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  metricsSummary: (ns = DEFAULT_NS, window = 500) =>
    request<{
      namespaceId: string;
      window: number;
      sampleCount: number;
      overview: MetricStats;
      routes: Array<MetricStats & { routeName: string }>;
      candidates: Array<
        MetricStats & { provider: string; model: string; key: string }
      >;
    }>(`/v1/${ns}/metrics/summary?window=${window}`),
  metricsRoute: (ns: string, name: string, window = 200) =>
    request<{
      routeName: string;
      overview: MetricStats;
      candidates: Array<MetricStats & { provider: string; model: string; key: string }>;
      recent: Array<{
        provider: string;
        model: string;
        latencyMs: number;
        success: boolean;
        costUnits: number;
        recordedAt: string;
      }>;
    }>(`/v1/${ns}/metrics/routes/${encodeURIComponent(name)}?window=${window}`),
  metricsRecent: (ns = DEFAULT_NS, limit = 50) =>
    request<{
      samples: Array<{
        routeName: string;
        provider: string;
        model: string;
        latencyMs: number;
        success: boolean;
        costUnits: number;
        recordedAt: string;
      }>;
    }>(`/v1/${ns}/metrics/recent?limit=${limit}`),
  tuneModelRoute: (ns: string, name: string, apply = true) =>
    request<Record<string, unknown>>(
      `/v1/${ns}/model-routes/${encodeURIComponent(name)}/tune?apply=${apply}`,
      { method: "POST" },
    ),
  gitSync: (
    ns: string,
    body: { directory: string; publish?: boolean; author?: string },
  ) =>
    request<{
      repo_id: string;
      applied: number;
      skipped: number;
      errors: string[];
      commit: string | null;
    }>(`/v1/${ns}/git-sync`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  listGitRepos: (ns = DEFAULT_NS) =>
    request<{
      repos: Array<{
        id: string;
        repoPath: string;
        branch: string;
        lastSyncAt: string | null;
        lastCommit: string | null;
        status: string;
        createdAt: string;
      }>;
    }>(`/v1/${ns}/git-sync/repos`),
  gitExport: (ns: string, directory: string) =>
    request<{ exported: number; directory: string }>(`/v1/${ns}/git-export`, {
      method: "POST",
      body: JSON.stringify({ directory }),
    }),
  terraformPreview: (ns = DEFAULT_NS) =>
    request<{
      namespace: string;
      resourceCount: number;
      files: Record<string, string>;
    }>(`/v1/${ns}/terraform/preview`),
  terraformExport: (ns: string, directory: string, write = true) =>
    request<{
      exported: number;
      directory: string | null;
      wrote: boolean;
      files: string[];
      preview: Record<string, string>;
    }>(`/v1/${ns}/terraform/export`, {
      method: "POST",
      body: JSON.stringify({ directory, write }),
    }),
};

