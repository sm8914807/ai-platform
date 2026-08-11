const API_BASE = import.meta.env.VITE_API_BASE ?? "";

export const DEFAULT_NS = "default-org/default-project";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
    },
  });
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

export const api = {
  health: () => request<Health>("/health"),
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
  publishResource: (ns: string, kind: string, name: string, version: string) =>
    request<Record<string, unknown>>(`/v1/${ns}/${kind}/${name}/publish`, {
      method: "POST",
      body: JSON.stringify({ version, principal: "console" }),
    }),
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
  planWorkflow: (ns: string, goal: string) =>
    request<{
      workflow_id: string;
      status: string;
      ir: {
        name: string;
        description?: string;
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
      body: JSON.stringify({ goal }),
    }),
  listCompliance: () =>
    request<{ packs: CompliancePack[] }>("/v1/compliance/packs"),
  listRegions: () =>
    request<{
      regions: Array<{ name: string; endpoint: string; is_primary: boolean; status: string }>;
    }>("/v1/regions"),
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
};
