import type { ReactNode } from "react";
import type { Resource } from "./api";

export type Spec = Record<string, unknown>;

export function resourceOptions(resources: Resource[], kind: string): string[] {
  const map: Record<string, string> = {
    Agent: "agents",
    Prompt: "prompts",
    Toolbox: "toolboxes",
    ModelRoute: "models",
    Tool: "tools",
    KnowledgeSource: "knowledge",
    Guardrail: "guardrails",
    Policy: "policies",
    Workflow: "workflows",
  };
  const prefix = map[kind] ?? `${kind.toLowerCase()}s`;
  return resources.filter((r) => r.kind === kind).map((r) => `${prefix}/${r.name}`);
}

export function defaultSpec(kind: string): Spec {
  switch (kind) {
    case "Agent":
      return {
        role: "executor",
        modelRef: "models/gpt-4o-routed",
        promptRef: "prompts/support-v3",
        toolboxRef: "",
      };
    case "Prompt":
      return {
        template: "You are a helpful assistant.\n\nUser: {{ input }}\n",
        variables: { input: { type: "string" } },
      };
    case "Tool":
      return {
        adapter: "mcp",
        manifest: {
          name: "get-customer",
          description: "Fetch customer via MCP",
          inputSchema: { type: "object", properties: { customerId: { type: "string" } } },
          outputSchema: { type: "object" },
        },
        config: {
          transport: "stdio",
          server: "crm-mcp",
          command: "npx",
          args: ["-y", "@modelcontextprotocol/server-everything"],
          toolName: "get-customer",
        },
      };
    case "Toolbox":
      return { tools: [{ ref: "tools/get-customer", permissions: ["read:customer"] }] };
    case "Workflow":
      return {
        trigger: { type: "event", event: "user.created" },
        steps: [
          { id: "step1", type: "agent", ref: "agents/support-agent", timeout: "30s" },
        ],
      };
    case "ModelRoute":
      return {
        strategy: "weightedFallback",
        candidates: [{ provider: "mock", model: "mock-1", weight: 100, fallback: true }],
        caching: { enabled: true, ttl: 3600 },
      };
    case "Policy":
      return {
        rules: [
          {
            effect: "allow",
            principals: ["*"],
            actions: ["agent:run"],
            resources: ["agents/*"],
          },
        ],
      };
    case "Guardrail":
      return {
        type: "pii_mask",
        config: { entities: ["email", "phone"] },
      };
    case "KnowledgeSource":
      return {
        retrieval: { topK: 5, hybrid: true },
        citations: { enabled: true },
        documents: [
          {
            id: "doc-1",
            text: "Product FAQ: billing, refunds, and onboarding.",
            metadata: { product: "support" },
          },
        ],
      };
    case "MemoryProfile":
      return {
        layers: [
          { type: "conversation", backend: "memory", ttl: "24h", maxTokens: 32000 },
        ],
        summarization: { enabled: false },
        versioning: true,
      };
    case "Environment":
      return {
        promotionFrom: "staging",
        requireApproval: true,
        approvers: ["team:platform-admins"],
        bundlePolicy: "signed-only",
      };
    case "EvaluationSuite":
      return {
        dataset: [
          {
            id: "case-1",
            input: { message: "billing invoice help" },
            expected: { contains: "billing" },
          },
        ],
        evaluators: [
          { type: "keyword_match" },
          { type: "llm_judge", criteria: "quality", metric: "quality" },
          { type: "latency", maxP95Ms: 5000 },
        ],
        triggers: [{ onPublish: ["agents/support-agent"] }],
        gates: { minScore: 0.5, failIf: "score < 0.5", metrics: { quality: 0.5 } },
      };
    default:
      return {};
  }
}

function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: ReactNode;
}) {
  return (
    <label className="form-field">
      <span className="form-label">{label}</span>
      {hint && <span className="form-hint muted">{hint}</span>}
      {children}
    </label>
  );
}

function RefSelect({
  label,
  value,
  options,
  onChange,
  allowCustom = true,
  placeholder = "Select or type ref…",
}: {
  label: string;
  value: string;
  options: string[];
  onChange: (v: string) => void;
  allowCustom?: boolean;
  placeholder?: string;
}) {
  return (
    <Field label={label}>
      {options.length > 0 ? (
        <select value={value} onChange={(e) => onChange(e.target.value)}>
          <option value="">— none —</option>
          {options.map((o) => (
            <option key={o} value={o}>
              {o}
            </option>
          ))}
          {allowCustom && value && !options.includes(value) && (
            <option value={value}>{value}</option>
          )}
        </select>
      ) : (
        <input value={value} onChange={(e) => onChange(e.target.value)} placeholder={placeholder} />
      )}
      {options.length > 0 && allowCustom && (
        <input
          className="form-ref-custom"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder={placeholder}
        />
      )}
    </Field>
  );
}

function AgentForm({
  spec,
  onChange,
  resources,
}: {
  spec: Spec;
  onChange: (s: Spec) => void;
  resources: Resource[];
}) {
  const set = (key: string, val: unknown) => onChange({ ...spec, [key]: val || undefined });

  return (
    <div className="resource-form">
      <Field label="Role" hint="What this agent does in a workflow">
        <select
          value={String(spec.role ?? "executor")}
          onChange={(e) => set("role", e.target.value)}
        >
          {["executor", "planner", "supervisor", "reviewer", "reflector", "router"].map((r) => (
            <option key={r} value={r}>
              {r}
            </option>
          ))}
        </select>
      </Field>
      <RefSelect
        label="Model"
        value={String(spec.modelRef ?? "")}
        options={resourceOptions(resources, "ModelRoute")}
        onChange={(v) => set("modelRef", v)}
        placeholder="models/gpt-4o-routed"
      />
      <RefSelect
        label="Prompt"
        value={String(spec.promptRef ?? "")}
        options={resourceOptions(resources, "Prompt")}
        onChange={(v) => set("promptRef", v)}
        placeholder="prompts/support-v3"
      />
      <RefSelect
        label="Toolbox (optional)"
        value={String(spec.toolboxRef ?? "")}
        options={resourceOptions(resources, "Toolbox")}
        onChange={(v) => set("toolboxRef", v)}
      />
      <Field label="Memory ref (optional)">
        <input
          value={String(spec.memoryRef ?? "")}
          onChange={(e) => set("memoryRef", e.target.value)}
          placeholder="memory/session-redis"
        />
      </Field>
      <Field label="Knowledge refs" hint="Comma-separated">
        <input
          value={Array.isArray(spec.knowledgeRefs) ? (spec.knowledgeRefs as string[]).join(", ") : ""}
          onChange={(e) =>
            set(
              "knowledgeRefs",
              e.target.value
                .split(",")
                .map((s) => s.trim())
                .filter(Boolean),
            )
          }
          placeholder="knowledge/kb-product-docs"
        />
      </Field>
      <Field label="Guardrails" hint="Comma-separated">
        <input
          value={Array.isArray(spec.guardrails) ? (spec.guardrails as string[]).join(", ") : ""}
          onChange={(e) =>
            set(
              "guardrails",
              e.target.value
                .split(",")
                .map((s) => s.trim())
                .filter(Boolean),
            )
          }
          placeholder="guardrails/pii-mask"
        />
      </Field>
      <div className="form-section-title">Multi-agent collaboration</div>
      <Field label="Pattern">
        <select
          value={String((spec.collaboration as Spec | undefined)?.pattern ?? "")}
          onChange={(e) => {
            const pattern = e.target.value;
            if (!pattern) {
              const next = { ...spec };
              delete next.collaboration;
              onChange(next);
              return;
            }
            const prev = (spec.collaboration as Spec) ?? {};
            const agents =
              (prev.agents as Spec) ??
              (pattern === "planner_executor_reviewer"
                ? {
                    planner: "agents/planner-agent",
                    executor: "agents/executor-agent",
                    reviewer: "agents/reviewer-agent",
                  }
                : pattern === "peer_round_robin"
                  ? { a: "agents/planner-agent", b: "agents/executor-agent" }
                  : { supervisor: "agents/support-agent", worker: "agents/executor-agent" });
            onChange({
              ...spec,
              collaboration: {
                ...prev,
                pattern,
                maxIterations: Number(prev.maxIterations ?? 2),
                sharedContext: prev.sharedContext !== false,
                contextScope: String(prev.contextScope ?? "session"),
                agents,
              },
            });
          }}
        >
          <option value="">None (single agent)</option>
          <option value="planner_executor_reviewer">planner → executor → reviewer</option>
          <option value="supervisor_workers">supervisor / workers</option>
          <option value="hierarchical">hierarchical</option>
          <option value="peer_round_robin">peer round-robin</option>
        </select>
      </Field>
      {Boolean((spec.collaboration as Spec | undefined)?.pattern) && (
        <>
          <Field label="Max iterations">
            <input
              type="number"
              min={1}
              max={8}
              value={Number((spec.collaboration as Spec).maxIterations ?? 2)}
              onChange={(e) =>
                onChange({
                  ...spec,
                  collaboration: {
                    ...(spec.collaboration as Spec),
                    maxIterations: Number(e.target.value),
                  },
                })
              }
            />
          </Field>
          <Field label="Role → agent refs" hint='JSON object, e.g. {"planner":"agents/planner-agent"}'>
            <textarea
              rows={4}
              value={JSON.stringify(
                ((spec.collaboration as Spec).agents as Spec) ?? {},
                null,
                2,
              )}
              onChange={(e) => {
                try {
                  const agents = JSON.parse(e.target.value) as Spec;
                  onChange({
                    ...spec,
                    collaboration: { ...(spec.collaboration as Spec), agents },
                  });
                } catch {
                  /* keep typing */
                }
              }}
            />
          </Field>
        </>
      )}
    </div>
  );
}

function PromptForm({ spec, onChange }: { spec: Spec; onChange: (s: Spec) => void }) {
  return (
    <div className="resource-form">
      <Field label="Template" hint="Use {{ variable }} for inputs">
        <textarea
          rows={8}
          value={String(spec.template ?? "")}
          onChange={(e) => onChange({ ...spec, template: e.target.value })}
          placeholder="You are a support agent…"
        />
      </Field>
      <Field label="Main variable name" hint="Shown in template as {{ name }}">
        <input
          value={
            spec.variables && typeof spec.variables === "object"
              ? Object.keys(spec.variables as object)[0] ?? "input"
              : "input"
          }
          onChange={(e) =>
            onChange({
              ...spec,
              variables: { [e.target.value || "input"]: { type: "string" } },
            })
          }
        />
      </Field>
    </div>
  );
}

function ToolForm({ spec, onChange }: { spec: Spec; onChange: (s: Spec) => void }) {
  const manifest = (spec.manifest as Spec) ?? {};
  const config = (spec.config as Spec) ?? {};

  const setManifest = (key: string, val: unknown) =>
    onChange({ ...spec, manifest: { ...manifest, [key]: val } });
  const setConfig = (key: string, val: unknown) =>
    onChange({ ...spec, config: { ...config, [key]: val } });

  return (
    <div className="resource-form">
      <Field label="Adapter">
        <select
          value={String(spec.adapter ?? "rest")}
          onChange={(e) => onChange({ ...spec, adapter: e.target.value })}
        >
          {["rest", "mcp", "openapi", "graphql", "grpc", "cli", "custom"].map((a) => (
            <option key={a} value={a}>
              {a}
            </option>
          ))}
        </select>
      </Field>
      <Field label="Tool name">
        <input
          value={String(manifest.name ?? "")}
          onChange={(e) => setManifest("name", e.target.value)}
        />
      </Field>
      <Field label="Description">
        <input
          value={String(manifest.description ?? "")}
          onChange={(e) => setManifest("description", e.target.value)}
        />
      </Field>
      {spec.adapter === "rest" && (
        <>
          <Field label="URL" hint="External API endpoint (e.g. MFL products search)">
            <input
              value={String(config.url ?? "")}
              onChange={(e) => setConfig("url", e.target.value)}
              placeholder="https://api.example.com/api/Products/search"
            />
          </Field>
          <Field label="HTTP method">
            <select
              value={String(config.method ?? "GET")}
              onChange={(e) => setConfig("method", e.target.value)}
            >
              {["GET", "POST", "PUT", "PATCH", "DELETE"].map((m) => (
                <option key={m} value={m}>
                  {m}
                </option>
              ))}
            </select>
          </Field>
        </>
      )}
      {spec.adapter === "mcp" && (
        <>
          <Field label="Logical server name">
            <input
              value={String(config.server ?? "")}
              onChange={(e) => setConfig("server", e.target.value)}
              placeholder="crm-mcp"
            />
          </Field>
          <Field label="Transport">
            <select
              value={String(config.transport ?? "stdio")}
              onChange={(e) => setConfig("transport", e.target.value)}
            >
              <option value="stdio">stdio (local process)</option>
              <option value="http">http (Streamable HTTP)</option>
            </select>
          </Field>
          <Field label="Remote MCP tool name" hint="Defaults to manifest name">
            <input
              value={String(config.toolName ?? "")}
              onChange={(e) => setConfig("toolName", e.target.value)}
              placeholder="get-customer"
            />
          </Field>
          {String(config.transport ?? "stdio") === "http" ? (
            <Field label="MCP URL" hint="Streamable HTTP endpoint">
              <input
                value={String(config.url ?? "")}
                onChange={(e) => setConfig("url", e.target.value)}
                placeholder="https://mcp.example.com/mcp"
              />
            </Field>
          ) : (
            <>
              <Field label="Command" hint="Allowlisted binary (npx, python3, uvx, docker…)">
                <input
                  value={String(config.command ?? "")}
                  onChange={(e) => setConfig("command", e.target.value)}
                  placeholder="npx"
                />
              </Field>
              <Field label="Args" hint="Comma-separated">
                <input
                  value={
                    Array.isArray(config.args)
                      ? (config.args as string[]).join(", ")
                      : String(config.args ?? "")
                  }
                  onChange={(e) =>
                    setConfig(
                      "args",
                      e.target.value
                        .split(",")
                        .map((s) => s.trim())
                        .filter(Boolean),
                    )
                  }
                  placeholder="-y, @modelcontextprotocol/server-everything"
                />
              </Field>
            </>
          )}
          <Field label="Auth secret ref" hint="Optional secrets/name for API key">
            <input
              value={String(spec.authRef ?? config.authRef ?? "")}
              onChange={(e) => onChange({ ...spec, authRef: e.target.value })}
              placeholder="secrets/mcp-token"
            />
          </Field>
        </>
      )}
    </div>
  );
}

type WfStep = {
  id: string;
  type: string;
  ref?: string;
  when?: string;
  timeout?: string;
};

function WorkflowForm({
  spec,
  onChange,
  resources,
}: {
  spec: Spec;
  onChange: (s: Spec) => void;
  resources: Resource[];
}) {
  const trigger = (spec.trigger as Spec) ?? {};
  const steps = (Array.isArray(spec.steps) ? spec.steps : []) as WfStep[];

  const setTrigger = (key: string, val: string) =>
    onChange({ ...spec, trigger: { ...trigger, [key]: val } });

  const setStep = (idx: number, patch: Partial<WfStep>) => {
    const next = steps.map((s, i) => (i === idx ? { ...s, ...patch } : s));
    onChange({ ...spec, steps: next });
  };

  const addStep = () =>
    onChange({
      ...spec,
      steps: [...steps, { id: `step${steps.length + 1}`, type: "agent", ref: "" }],
    });

  const removeStep = (idx: number) =>
    onChange({ ...spec, steps: steps.filter((_, i) => i !== idx) });

  const agentRefs = resourceOptions(resources, "Agent");

  return (
    <div className="resource-form">
      <Field label="Trigger type">
        <select
          value={String(trigger.type ?? "event")}
          onChange={(e) => setTrigger("type", e.target.value)}
        >
          <option value="event">event</option>
          <option value="manual">manual</option>
          <option value="schedule">schedule</option>
        </select>
      </Field>
      <Field label="Trigger event / name">
        <input
          value={String(trigger.event ?? trigger.name ?? "")}
          onChange={(e) => setTrigger("event", e.target.value)}
          placeholder="user.created"
        />
      </Field>
      <div className="form-section-title">Steps</div>
      {steps.map((step, idx) => (
        <div key={idx} className="wf-step-card">
          <div className="form-row compact">
            <input
              value={step.id}
              onChange={(e) => setStep(idx, { id: e.target.value })}
              placeholder="step id"
            />
            <select
              value={step.type}
              onChange={(e) => setStep(idx, { type: e.target.value })}
            >
              {["agent", "tool", "humanApproval", "parallel", "workflow"].map((t) => (
                <option key={t} value={t}>
                  {t}
                </option>
              ))}
            </select>
            <button type="button" className="danger ghost" onClick={() => removeStep(idx)}>
              Remove
            </button>
          </div>
          <input
            value={step.ref ?? ""}
            onChange={(e) => setStep(idx, { ref: e.target.value })}
            placeholder="agents/support-agent"
            list={`agent-refs-${idx}`}
          />
          <datalist id={`agent-refs-${idx}`}>
            {agentRefs.map((r) => (
              <option key={r} value={r} />
            ))}
          </datalist>
          {step.type === "agent" && (
            <input
              value={step.when ?? ""}
              onChange={(e) => setStep(idx, { when: e.target.value || undefined })}
              placeholder="when (optional): $.steps.approve.status == approved"
            />
          )}
        </div>
      ))}
      <button type="button" onClick={addStep}>
        + Add step
      </button>
    </div>
  );
}

function ToolboxForm({
  spec,
  onChange,
  resources,
}: {
  spec: Spec;
  onChange: (s: Spec) => void;
  resources: Resource[];
}) {
  const tools = (Array.isArray(spec.tools) ? spec.tools : []) as Array<{
    ref: string;
    permissions: string[];
  }>;
  const toolRefs = resourceOptions(resources, "Tool");

  const setTool = (idx: number, ref: string) => {
    const next = tools.map((t, i) => (i === idx ? { ...t, ref } : t));
    onChange({ ...spec, tools: next });
  };

  const addTool = () =>
    onChange({
      ...spec,
      tools: [...tools, { ref: toolRefs[0] ?? "tools/my-tool", permissions: ["read"] }],
    });

  return (
    <div className="resource-form">
      <div className="form-section-title">Tools in this toolbox</div>
      {tools.map((t, idx) => (
        <div key={idx} className="form-row compact">
          <select value={t.ref} onChange={(e) => setTool(idx, e.target.value)}>
            {toolRefs.map((r) => (
              <option key={r} value={r}>
                {r}
              </option>
            ))}
          </select>
          <button
            type="button"
            className="danger ghost"
            onClick={() =>
              onChange({ ...spec, tools: tools.filter((_, i) => i !== idx) })
            }
          >
            Remove
          </button>
        </div>
      ))}
      <button type="button" onClick={addTool}>
        + Add tool
      </button>
    </div>
  );
}

type ModelCandidate = {
  provider: string;
  model: string;
  weight?: number;
  fallback?: boolean;
  maxLatencyMs?: number;
};

function ModelRouteForm({ spec, onChange }: { spec: Spec; onChange: (s: Spec) => void }) {
  const candidates = (Array.isArray(spec.candidates) ? spec.candidates : []) as ModelCandidate[];
  const caching = (spec.caching as Spec) ?? {};

  const updateCandidate = (idx: number, patch: Partial<ModelCandidate>) =>
    onChange({
      ...spec,
      candidates: candidates.map((candidate, i) =>
        i === idx ? { ...candidate, ...patch } : candidate,
      ),
    });

  return (
    <div className="resource-form">
      <Field label="Routing strategy" hint="How the runtime chooses a provider">
        <select
          value={String(spec.strategy ?? "weightedFallback")}
          onChange={(e) => onChange({ ...spec, strategy: e.target.value })}
        >
          {["weightedFallback", "costOptimized", "latencyOptimized", "capabilityMatch"].map(
            (strategy) => (
              <option key={strategy} value={strategy}>
                {strategy}
              </option>
            ),
          )}
        </select>
      </Field>

      <div className="form-section-title">Model candidates</div>
      {candidates.map((candidate, idx) => (
        <div key={idx} className="wf-step-card">
          <div className="form-row compact">
            <input
              value={candidate.provider}
              onChange={(e) => updateCandidate(idx, { provider: e.target.value })}
              placeholder="provider (openai, anthropic, mock)"
            />
            <input
              value={candidate.model}
              onChange={(e) => updateCandidate(idx, { model: e.target.value })}
              placeholder="model name"
            />
            <button
              type="button"
              className="danger ghost"
              onClick={() =>
                onChange({ ...spec, candidates: candidates.filter((_, i) => i !== idx) })
              }
            >
              Remove
            </button>
          </div>
          <div className="form-row compact">
            <Field label="Weight">
              <input
                type="number"
                min={0}
                value={candidate.weight ?? 100}
                onChange={(e) => updateCandidate(idx, { weight: Number(e.target.value) })}
              />
            </Field>
            <Field label="Max latency (ms)">
              <input
                type="number"
                min={0}
                value={candidate.maxLatencyMs ?? ""}
                onChange={(e) =>
                  updateCandidate(idx, {
                    maxLatencyMs: e.target.value ? Number(e.target.value) : undefined,
                  })
                }
                placeholder="optional"
              />
            </Field>
            <label className="check-field">
              <input
                type="checkbox"
                checked={Boolean(candidate.fallback)}
                onChange={(e) => updateCandidate(idx, { fallback: e.target.checked })}
              />
              Fallback
            </label>
          </div>
        </div>
      ))}
      <button
        type="button"
        onClick={() =>
          onChange({
            ...spec,
            candidates: [
              ...candidates,
              { provider: "openai", model: "gpt-4o-mini", weight: 100, fallback: false },
            ],
          })
        }
      >
        + Add candidate
      </button>

      <div className="form-section-title">Response cache</div>
      <label className="check-field">
        <input
          type="checkbox"
          checked={Boolean(caching.enabled)}
          onChange={(e) =>
            onChange({ ...spec, caching: { ...caching, enabled: e.target.checked } })
          }
        />
        Enable caching
      </label>
      <Field label="Cache TTL (seconds)">
        <input
          type="number"
          min={0}
          value={Number(caching.ttl ?? 3600)}
          onChange={(e) =>
            onChange({ ...spec, caching: { ...caching, ttl: Number(e.target.value) } })
          }
        />
      </Field>
    </div>
  );
}

type PolicyRule = {
  effect: "allow" | "deny";
  principals?: string[];
  actions: string[];
  resources: string[];
};

function PolicyForm({ spec, onChange }: { spec: Spec; onChange: (s: Spec) => void }) {
  const rules = (Array.isArray(spec.rules) ? spec.rules : []) as PolicyRule[];
  const csv = (value?: string[]) => (value ?? []).join(", ");
  const parseCsv = (value: string) =>
    value
      .split(",")
      .map((item) => item.trim())
      .filter(Boolean);
  const updateRule = (idx: number, patch: Partial<PolicyRule>) =>
    onChange({
      ...spec,
      rules: rules.map((rule, i) => (i === idx ? { ...rule, ...patch } : rule)),
    });

  return (
    <div className="resource-form">
      <p className="muted form-hint">
        Rules are checked before an agent or tool runs. Put specific deny rules before broad allows.
      </p>
      {rules.map((rule, idx) => (
        <div key={idx} className="wf-step-card">
          <div className="form-row compact">
            <Field label="Effect">
              <select
                value={rule.effect}
                onChange={(e) =>
                  updateRule(idx, { effect: e.target.value as PolicyRule["effect"] })
                }
              >
                <option value="allow">allow</option>
                <option value="deny">deny</option>
              </select>
            </Field>
            <button
              type="button"
              className="danger ghost"
              onClick={() => onChange({ ...spec, rules: rules.filter((_, i) => i !== idx) })}
            >
              Remove
            </button>
          </div>
          <Field label="Principals" hint="Comma-separated users, roles, or *">
            <input
              value={csv(rule.principals)}
              onChange={(e) => updateRule(idx, { principals: parseCsv(e.target.value) })}
              placeholder="role:support, user@example.com, *"
            />
          </Field>
          <Field label="Actions" hint="Comma-separated">
            <input
              value={csv(rule.actions)}
              onChange={(e) => updateRule(idx, { actions: parseCsv(e.target.value) })}
              placeholder="agent:run, tool:invoke"
            />
          </Field>
          <Field label="Resources" hint="Comma-separated refs or wildcards">
            <input
              value={csv(rule.resources)}
              onChange={(e) => updateRule(idx, { resources: parseCsv(e.target.value) })}
              placeholder="agents/*, tools/mfl-search"
            />
          </Field>
        </div>
      ))}
      <button
        type="button"
        onClick={() =>
          onChange({
            ...spec,
            rules: [
              ...rules,
              { effect: "allow", principals: ["*"], actions: ["agent:run"], resources: ["agents/*"] },
            ],
          })
        }
      >
        + Add rule
      </button>
    </div>
  );
}

function GuardrailForm({ spec, onChange }: { spec: Spec; onChange: (s: Spec) => void }) {
  const config = (spec.config as Spec) ?? {};
  const setConfig = (key: string, value: unknown) =>
    onChange({ ...spec, config: { ...config, [key]: value } });
  const type = String(spec.type ?? "pii_mask");

  return (
    <div className="resource-form">
      <Field label="Guardrail type">
        <select value={type} onChange={(e) => onChange({ ...spec, type: e.target.value, config: {} })}>
          <option value="pii_mask">PII masking</option>
          <option value="injection_detect">Prompt injection detection</option>
          <option value="content_moderation">Content moderation</option>
          <option value="custom">Custom</option>
        </select>
      </Field>
      {type === "pii_mask" && (
        <Field label="PII entities" hint="Comma-separated">
          <input
            value={Array.isArray(config.entities) ? (config.entities as string[]).join(", ") : ""}
            onChange={(e) =>
              setConfig(
                "entities",
                e.target.value
                  .split(",")
                  .map((item) => item.trim())
                  .filter(Boolean),
              )
            }
            placeholder="email, phone, credit_card"
          />
        </Field>
      )}
      {type === "injection_detect" && (
        <Field label="On detection">
          <select
            value={String(config.action ?? "alert")}
            onChange={(e) => setConfig("action", e.target.value)}
          >
            <option value="alert">Alert and continue</option>
            <option value="block">Block the request</option>
          </select>
        </Field>
      )}
      {type === "content_moderation" && (
        <>
          <p className="muted form-hint">
            Configuration is stored now; connect a content-moderation runtime plugin to enforce it.
          </p>
          <Field label="Blocked categories" hint="Comma-separated">
            <input
              value={Array.isArray(config.categories) ? (config.categories as string[]).join(", ") : ""}
              onChange={(e) =>
                setConfig(
                  "categories",
                  e.target.value
                    .split(",")
                    .map((item) => item.trim())
                    .filter(Boolean),
                )
              }
              placeholder="hate, violence, sexual"
            />
          </Field>
          <Field label="Threshold (0–1)">
            <input
              type="number"
              min={0}
              max={1}
              step={0.1}
              value={Number(config.threshold ?? 0.7)}
              onChange={(e) => setConfig("threshold", Number(e.target.value))}
            />
          </Field>
        </>
      )}
      {type === "custom" && (
        <Field label="Handler reference">
          <input
            value={String(config.handlerRef ?? "")}
            onChange={(e) => setConfig("handlerRef", e.target.value)}
            placeholder="tools/custom-guardrail"
          />
        </Field>
      )}
    </div>
  );
}

function KnowledgeSourceForm({ spec, onChange }: { spec: Spec; onChange: (s: Spec) => void }) {
  const retrieval = (spec.retrieval as Spec) ?? {};
  const citations = (spec.citations as Spec) ?? {};
  const documents = (Array.isArray(spec.documents) ? spec.documents : []) as Spec[];

  function setRetrieval(key: string, value: unknown) {
    onChange({ ...spec, retrieval: { ...retrieval, [key]: value } });
  }

  function updateDoc(i: number, patch: Spec) {
    const next = documents.map((d, idx) => (idx === i ? { ...d, ...patch } : d));
    onChange({ ...spec, documents: next });
  }

  return (
    <div className="resource-form">
      <Field label="Top K" hint="Chunks returned per query">
        <input
          type="number"
          min={1}
          value={Number(retrieval.topK ?? 5)}
          onChange={(e) => setRetrieval("topK", Number(e.target.value))}
        />
      </Field>
      <label className="form-check">
        <input
          type="checkbox"
          checked={Boolean(retrieval.hybrid ?? true)}
          onChange={(e) => setRetrieval("hybrid", e.target.checked)}
        />
        Hybrid retrieval
      </label>
      <label className="form-check">
        <input
          type="checkbox"
          checked={Boolean(citations.enabled ?? true)}
          onChange={(e) =>
            onChange({ ...spec, citations: { ...citations, enabled: e.target.checked } })
          }
        />
        Include citations
      </label>
      <div className="form-section-title">Documents</div>
      {documents.map((doc, i) => (
        <div key={i} className="form-card">
          <Field label="Document id">
            <input
              value={String(doc.id ?? "")}
              onChange={(e) => updateDoc(i, { id: e.target.value })}
            />
          </Field>
          <Field label="Text">
            <textarea
              rows={4}
              value={String(doc.text ?? "")}
              onChange={(e) => updateDoc(i, { text: e.target.value })}
            />
          </Field>
          <button
            type="button"
            className="ghost"
            onClick={() =>
              onChange({ ...spec, documents: documents.filter((_, idx) => idx !== i) })
            }
          >
            Remove document
          </button>
        </div>
      ))}
      <button
        type="button"
        onClick={() =>
          onChange({
            ...spec,
            documents: [...documents, { id: `doc-${documents.length + 1}`, text: "", metadata: {} }],
          })
        }
      >
        Add document
      </button>
    </div>
  );
}

function MemoryProfileForm({ spec, onChange }: { spec: Spec; onChange: (s: Spec) => void }) {
  const layers = (Array.isArray(spec.layers) ? spec.layers : []) as Spec[];
  const layer = layers[0] ?? { type: "conversation", backend: "memory", ttl: "24h", maxTokens: 32000 };

  function setLayer(patch: Spec) {
    onChange({ ...spec, layers: [{ ...layer, ...patch }] });
  }

  return (
    <div className="resource-form">
      <Field label="Layer type">
        <select value={String(layer.type ?? "conversation")} onChange={(e) => setLayer({ type: e.target.value })}>
          {["conversation", "semantic", "entity", "session", "episodic"].map((t) => (
            <option key={t} value={t}>
              {t}
            </option>
          ))}
        </select>
      </Field>
      <Field label="Backend" hint="memory (default) or redis ref">
        <input value={String(layer.backend ?? "memory")} onChange={(e) => setLayer({ backend: e.target.value })} />
      </Field>
      <Field label="TTL">
        <input value={String(layer.ttl ?? "24h")} onChange={(e) => setLayer({ ttl: e.target.value })} />
      </Field>
      <Field label="Max tokens">
        <input
          type="number"
          value={Number(layer.maxTokens ?? 32000)}
          onChange={(e) => setLayer({ maxTokens: Number(e.target.value) })}
        />
      </Field>
      <label className="form-check">
        <input
          type="checkbox"
          checked={Boolean(spec.versioning ?? false)}
          onChange={(e) => onChange({ ...spec, versioning: e.target.checked })}
        />
        Version memory entries
      </label>
    </div>
  );
}

function EnvironmentForm({ spec, onChange }: { spec: Spec; onChange: (s: Spec) => void }) {
  const approvers = Array.isArray(spec.approvers) ? (spec.approvers as string[]) : [];
  return (
    <div className="resource-form">
      <Field label="Promotion from" hint="Source environment name">
        <input
          value={String(spec.promotionFrom ?? "")}
          onChange={(e) => onChange({ ...spec, promotionFrom: e.target.value })}
          placeholder="staging"
        />
      </Field>
      <label className="form-check">
        <input
          type="checkbox"
          checked={Boolean(spec.requireApproval ?? false)}
          onChange={(e) => onChange({ ...spec, requireApproval: e.target.checked })}
        />
        Require approval before promote
      </label>
      <Field label="Approvers" hint="Comma-separated principals">
        <input
          value={approvers.join(", ")}
          onChange={(e) =>
            onChange({
              ...spec,
              approvers: e.target.value
                .split(",")
                .map((s) => s.trim())
                .filter(Boolean),
            })
          }
          placeholder="team:platform-admins"
        />
      </Field>
      <Field label="Bundle policy">
        <input
          value={String(spec.bundlePolicy ?? "signed-only")}
          onChange={(e) => onChange({ ...spec, bundlePolicy: e.target.value })}
        />
      </Field>
    </div>
  );
}

function EvaluationSuiteForm({ spec, onChange }: { spec: Spec; onChange: (s: Spec) => void }) {
  const dataset = (Array.isArray(spec.dataset) ? spec.dataset : []) as Spec[];
  const gates = (spec.gates as Spec) ?? {};
  const evaluators = (Array.isArray(spec.evaluators) ? spec.evaluators : []) as Spec[];
  const triggers = (Array.isArray(spec.triggers) ? spec.triggers : []) as Spec[];
  const case0 = dataset[0] ?? { id: "case-1", input: { message: "" }, expected: { contains: "" } };
  const input = (case0.input as Spec) ?? {};
  const expected = (case0.expected as Spec) ?? {};
  const onPublish = ((triggers[0]?.onPublish as string[]) ?? ["agents/support-agent"]).join(", ");
  const hasLlm = evaluators.some((e) => String(e.type) === "llm_judge");

  return (
    <div className="resource-form">
      <div className="form-section-title">Golden case</div>
      <Field label="Case id">
        <input
          value={String(case0.id ?? "")}
          onChange={(e) => onChange({ ...spec, dataset: [{ ...case0, id: e.target.value }] })}
        />
      </Field>
      <Field label="Input message">
        <input
          value={String(input.message ?? "")}
          onChange={(e) =>
            onChange({
              ...spec,
              dataset: [{ ...case0, input: { ...input, message: e.target.value } }],
            })
          }
        />
      </Field>
      <Field label="Expected contains">
        <input
          value={String(expected.contains ?? "")}
          onChange={(e) =>
            onChange({
              ...spec,
              dataset: [{ ...case0, expected: { ...expected, contains: e.target.value } }],
            })
          }
        />
      </Field>
      <div className="form-section-title">Judges</div>
      <label className="check-row">
        <input
          type="checkbox"
          checked={hasLlm}
          onChange={(e) => {
            const rest = evaluators.filter((ev) => String(ev.type) !== "llm_judge");
            onChange({
              ...spec,
              evaluators: e.target.checked
                ? [...rest, { type: "llm_judge", criteria: "quality", metric: "quality" }]
                : rest.length
                  ? rest
                  : [{ type: "keyword_match" }],
            });
          }}
        />
        LLM judge (quality)
      </label>
      <Field label="Publish triggers (agent refs)">
        <input
          value={onPublish}
          onChange={(e) =>
            onChange({
              ...spec,
              triggers: [
                {
                  onPublish: e.target.value
                    .split(",")
                    .map((s) => s.trim())
                    .filter(Boolean),
                },
              ],
            })
          }
          placeholder="agents/support-agent"
        />
      </Field>
      <div className="form-section-title">Publish gate</div>
      <Field label="Minimum score">
        <input
          type="number"
          min={0}
          max={1}
          step={0.1}
          value={Number(gates.minScore ?? 0.5)}
          onChange={(e) =>
            onChange({ ...spec, gates: { ...gates, minScore: Number(e.target.value) } })
          }
        />
      </Field>
      <Field label="Fail if expression">
        <input
          value={String(gates.failIf ?? "score < 0.5")}
          onChange={(e) => onChange({ ...spec, gates: { ...gates, failIf: e.target.value } })}
        />
      </Field>
    </div>
  );
}

export function ResourceSpecForm({
  kind,
  spec,
  onChange,
  resources,
}: {
  kind: string;
  spec: Spec;
  onChange: (s: Spec) => void;
  resources: Resource[];
}) {
  switch (kind) {
    case "Agent":
      return <AgentForm spec={spec} onChange={onChange} resources={resources} />;
    case "Prompt":
      return <PromptForm spec={spec} onChange={onChange} />;
    case "Tool":
      return <ToolForm spec={spec} onChange={onChange} />;
    case "Workflow":
      return <WorkflowForm spec={spec} onChange={onChange} resources={resources} />;
    case "Toolbox":
      return <ToolboxForm spec={spec} onChange={onChange} resources={resources} />;
    case "ModelRoute":
      return <ModelRouteForm spec={spec} onChange={onChange} />;
    case "Policy":
      return <PolicyForm spec={spec} onChange={onChange} />;
    case "Guardrail":
      return <GuardrailForm spec={spec} onChange={onChange} />;
    case "KnowledgeSource":
      return <KnowledgeSourceForm spec={spec} onChange={onChange} />;
    case "MemoryProfile":
      return <MemoryProfileForm spec={spec} onChange={onChange} />;
    case "Environment":
      return <EnvironmentForm spec={spec} onChange={onChange} />;
    case "EvaluationSuite":
      return <EvaluationSuiteForm spec={spec} onChange={onChange} />;
    default:
      return (
        <p className="muted">
          No visual form for <strong>{kind}</strong> yet — use the JSON tab.
        </p>
      );
  }
}

export function hasVisualForm(kind: string) {
  return [
    "Agent",
    "Prompt",
    "Tool",
    "Workflow",
    "Toolbox",
    "ModelRoute",
    "Policy",
    "Guardrail",
    "KnowledgeSource",
    "MemoryProfile",
    "Environment",
    "EvaluationSuite",
  ].includes(kind);
}

export function cleanSpec(spec: Spec): Spec {
  const out = { ...spec };
  if ("capabilities" in out) delete out.capabilities;
  // drop empty strings
  for (const [k, v] of Object.entries(out)) {
    if (v === "" || v === undefined) delete out[k];
    if (Array.isArray(v) && v.length === 0) delete out[k];
  }
  return out;
}
