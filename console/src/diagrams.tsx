import type { Resource } from "./api";

export type FlowNode = {
  id: string;
  label: string;
  sub?: string;
  kind?: string;
  status?: string;
  note?: string;
};

const KIND_CLASS: Record<string, string> = {
  trigger: "fn-trigger",
  agent: "fn-agent",
  tool: "fn-tool",
  humanApproval: "fn-approval",
  approval: "fn-approval",
  workflow: "fn-workflow",
  model: "fn-model",
  prompt: "fn-prompt",
  toolbox: "fn-toolbox",
  knowledge: "fn-knowledge",
  guardrail: "fn-guard",
  bus: "fn-bus",
  discovery: "fn-discovery",
  parallel: "fn-parallel",
};

export function FlowLane({
  nodes,
  title,
  empty = "No steps yet",
}: {
  nodes: FlowNode[];
  title?: string;
  empty?: string;
}) {
  if (!nodes.length) {
    return (
      <div className="flow-lane empty">
        {title && <div className="flow-lane-title">{title}</div>}
        <p className="muted">{empty}</p>
      </div>
    );
  }

  return (
    <div className="flow-lane">
      {title && <div className="flow-lane-title">{title}</div>}
      <div className="flow-track" role="list">
        {nodes.map((n, i) => (
          <div key={n.id} className="flow-item" role="listitem">
            {i > 0 && (
              <div className="flow-arrow" aria-hidden>
                <span className="flow-arrow-line" />
                <span className="flow-arrow-head">→</span>
              </div>
            )}
            <div
              className={`flow-node ${KIND_CLASS[n.kind ?? ""] ?? "fn-default"}`}
              title={n.note ?? n.sub}
            >
              <div className="flow-node-kind">{n.kind ?? "step"}</div>
              <div className="flow-node-label">{n.label}</div>
              {n.sub && <div className="flow-node-sub mono">{n.sub}</div>}
              {n.status && <span className={`badge ${statusBadge(n.status)}`}>{n.status}</span>}
              {n.note && <div className="flow-node-note">{n.note}</div>}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function statusBadge(status: string) {
  const s = status.toLowerCase();
  if (s.includes("fail") || s.includes("error")) return "warn";
  if (s.includes("ok") || s.includes("complete") || s.includes("success") || s === "online")
    return "ok";
  return "";
}

export function workflowToNodes(spec: {
  trigger?: { type?: string; event?: string };
  steps?: Array<{
    id: string;
    type?: string;
    ref?: string | null;
    when?: string | null;
    description?: string;
  }>;
}): FlowNode[] {
  const nodes: FlowNode[] = [];
  if (spec.trigger?.type || spec.trigger?.event) {
    nodes.push({
      id: "trigger",
      label: spec.trigger.event ?? spec.trigger.type ?? "trigger",
      sub: spec.trigger.type,
      kind: "trigger",
    });
  }
  for (const step of spec.steps ?? []) {
    nodes.push({
      id: step.id,
      label: step.id,
      sub: step.ref ?? undefined,
      kind: step.type ?? "agent",
      note: step.when ? `when: ${step.when}` : step.description,
    });
  }
  return nodes;
}

export function PlatformArchitecture() {
  return (
    <div className="arch-board">
      <FlowLane
        title="How work moves through the platform"
        nodes={[
          { id: "1", label: "CRDs", sub: "Agent · Prompt · Tool", kind: "workflow" },
          { id: "2", label: "Publish", sub: "versioned registry", kind: "discovery" },
          { id: "3", label: "Discovery", sub: "capabilities index", kind: "discovery" },
          { id: "4", label: "Workflow", sub: "plan → steps", kind: "workflow" },
          { id: "5", label: "Agents", sub: "planner · executor", kind: "agent" },
          { id: "6", label: "Models / Tools", sub: "routes · toolboxes", kind: "model" },
          { id: "7", label: "Traces", sub: "context graph", kind: "bus" },
        ]}
      />
      <p className="muted arch-caption">
        Build resources → publish → discover by capability → run workflows → agents call models/tools
        → decisions land in the context graph.
      </p>
    </div>
  );
}

export function AgentGraph({ resources }: { resources: Resource[] }) {
  const agents = resources.filter((r) => r.kind === "Agent");
  if (!agents.length) {
    return <p className="muted">No agents published yet.</p>;
  }

  return (
    <div className="agent-graph">
      {agents.map((a) => {
        const spec = a.spec;
        const deps: FlowNode[] = [
          {
            id: `${a.name}-self`,
            label: a.name,
            sub: String(spec.role ?? "agent"),
            kind: "agent",
          },
        ];
        if (typeof spec.modelRef === "string") {
          deps.push({
            id: `${a.name}-model`,
            label: shortRef(spec.modelRef),
            sub: "model",
            kind: "model",
          });
        }
        if (typeof spec.promptRef === "string") {
          deps.push({
            id: `${a.name}-prompt`,
            label: shortRef(spec.promptRef),
            sub: "prompt",
            kind: "prompt",
          });
        }
        if (typeof spec.toolboxRef === "string") {
          deps.push({
            id: `${a.name}-tb`,
            label: shortRef(spec.toolboxRef),
            sub: "toolbox",
            kind: "toolbox",
          });
        }
        const knowledge = Array.isArray(spec.knowledgeRefs)
          ? (spec.knowledgeRefs as string[])
          : [];
        for (const k of knowledge.slice(0, 2)) {
          deps.push({
            id: `${a.name}-kb-${k}`,
            label: shortRef(k),
            sub: "knowledge",
            kind: "knowledge",
          });
        }
        const guards = Array.isArray(spec.guardrails) ? (spec.guardrails as string[]) : [];
        for (const g of guards.slice(0, 2)) {
          deps.push({
            id: `${a.name}-g-${g}`,
            label: shortRef(g),
            sub: "guardrail",
            kind: "guardrail",
          });
        }

        const collab = spec.collaboration as
          | { pattern?: string; agents?: Record<string, string> }
          | undefined;

        return (
          <div key={a.name} className="agent-card-diagram">
            <FlowLane title={`Agent · ${a.name}`} nodes={deps} />
            {collab?.pattern && (
              <div className="collab-strip">
                <span className="badge">collaboration</span>
                <span className="mono">{collab.pattern}</span>
                <FlowLane
                  nodes={collaborationNodes(collab.pattern, collab.agents)}
                  empty=""
                />
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

function collaborationNodes(
  pattern: string,
  agents?: Record<string, string>,
): FlowNode[] {
  if (pattern === "planner_executor_reviewer") {
    return [
      {
        id: "p",
        label: "planner",
        sub: agents?.planner ?? "agents/planner-agent",
        kind: "agent",
      },
      {
        id: "e",
        label: "executor",
        sub: agents?.executor ?? "agents/executor-agent",
        kind: "agent",
      },
      {
        id: "r",
        label: "reviewer",
        sub: agents?.reviewer ?? "agents/reviewer-agent",
        kind: "agent",
        note: "loop until approved",
      },
    ];
  }
  return [
    { id: "pat", label: pattern, kind: "workflow", sub: "pattern" },
  ];
}

export function MessagingGraph({
  messages,
}: {
  messages: Array<{ id: string; sender: string; recipient: string; status: string }>;
}) {
  const edges = messages.slice(0, 8).map((m) => ({
    id: m.id,
    label: shortRef(m.sender),
    sub: `→ ${shortRef(m.recipient)}`,
    kind: "bus",
    status: m.status,
  }));
  return (
    <FlowLane
      title="Recent message hops"
      nodes={edges.length ? edges : []}
      empty="No messages on the bus yet."
    />
  );
}

export function DiscoveryMap({
  agents,
}: {
  agents: Array<{ agent_ref: string; capabilities: string[]; status: string }>;
}) {
  if (!agents.length) return <p className="muted">No discovered agents.</p>;
  return (
    <div className="discovery-map">
      {agents.map((a) => (
        <div key={a.agent_ref} className="cap-chip-row">
          <div className={`flow-node fn-agent compact`}>
            <div className="flow-node-label">{shortRef(a.agent_ref)}</div>
            <span className={`badge ${a.status === "online" ? "ok" : ""}`}>{a.status}</span>
          </div>
          <div className="cap-arrows">→</div>
          <div className="cap-list">
            {a.capabilities.length ? (
              a.capabilities.map((c) => (
                <span key={c} className="cap-pill">
                  {c}
                </span>
              ))
            ) : (
              <span className="muted">no capabilities</span>
            )}
          </div>
        </div>
      ))}
    </div>
  );
}

function shortRef(ref: string) {
  const i = ref.lastIndexOf("/");
  return i >= 0 ? ref.slice(i + 1) : ref;
}
