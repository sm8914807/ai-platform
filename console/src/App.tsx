import { useCallback, useEffect, useMemo, useState } from "react";
import {
  api,
  DEFAULT_NS,
  type CompliancePack,
  type DiscoveredAgent,
  type FederatedPeer,
  type Health,
  type PlatformMessage,
  type Resource,
  type SecretMeta,
  type Trace,
} from "./api";
import {
  AgentGraph,
  DiscoveryMap,
  FlowLane,
  MessagingGraph,
  PlatformArchitecture,
  workflowToNodes,
} from "./diagrams";
import "./styles.css";

type View =
  | "overview"
  | "maps"
  | "resources"
  | "editor"
  | "traces"
  | "discovery"
  | "workflows"
  | "messaging"
  | "secrets"
  | "federation"
  | "compliance";

const NAV_GROUPS: { label: string; items: { id: View; label: string }[] }[] = [
  {
    label: "Build",
    items: [
      { id: "overview", label: "Overview" },
      { id: "maps", label: "Flow maps" },
      { id: "resources", label: "Resources" },
      { id: "editor", label: "Resource editor" },
      { id: "workflows", label: "Dynamic flows" },
    ],
  },
  {
    label: "Runtime",
    items: [
      { id: "traces", label: "Context graph" },
      { id: "discovery", label: "Discovery" },
      { id: "messaging", label: "Message bus" },
      { id: "federation", label: "AMTP federation" },
    ],
  },
  {
    label: "Ops",
    items: [
      { id: "secrets", label: "Secrets" },
      { id: "compliance", label: "Compliance" },
    ],
  },
];

const COMMANDS: { id: View; label: string; hint: string }[] = NAV_GROUPS.flatMap((g) =>
  g.items.map((i) => ({ id: i.id, label: i.label, hint: g.label })),
);

export default function App() {
  const [view, setView] = useState<View>("overview");
  const [ns] = useState(DEFAULT_NS);
  const [health, setHealth] = useState<Health | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [cmdOpen, setCmdOpen] = useState(false);
  const [editTarget, setEditTarget] = useState<Resource | null>(null);

  useEffect(() => {
    api.health().then(setHealth).catch(() => setHealth(null));
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setCmdOpen(true);
      }
      if (e.key === "Escape") setCmdOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  return (
    <div className="app-root">
      <header className="topbar">
        <div className="topbar-brand">
          <span className="topbar-mark">AI</span>
          <span>Platform Studio</span>
        </div>
        <button className="cmd-btn" onClick={() => setCmdOpen(true)}>
          ⌘K Command
        </button>
        <div className="topbar-ns muted">
          <span className="badge ok">{health ? `v${health.version}` : "…"}</span>
          <span className="badge">{health?.sqlBackend ?? health?.registryBackend ?? "—"}</span>
          <span className="mono">{ns}</span>
        </div>
      </header>

      <div className="shell">
        <aside className="sidebar">
          {NAV_GROUPS.map((group) => (
            <div key={group.label}>
              <div className="nav-label">{group.label}</div>
              {group.items.map((item) => (
                <button
                  key={item.id}
                  className={view === item.id ? "nav-item active" : "nav-item"}
                  onClick={() => {
                    if (item.id !== "editor") setEditTarget(null);
                    setView(item.id);
                  }}
                >
                  {item.label}
                </button>
              ))}
            </div>
          ))}
        </aside>

        <main className="main">
          {error && (
            <div className="banner error" onClick={() => setError(null)}>
              {error}
            </div>
          )}
          {view === "overview" && <Overview ns={ns} health={health} onError={setError} go={setView} />}
          {view === "maps" && <MapsView ns={ns} onError={setError} go={setView} />}
          {view === "resources" && (
            <ResourcesView
              ns={ns}
              onError={setError}
              onEdit={(r) => {
                setEditTarget(r);
                setView("editor");
              }}
            />
          )}
          {view === "editor" && (
            <EditorView
              ns={ns}
              onError={setError}
              initial={editTarget}
              onPublished={() => {
                setEditTarget(null);
                setView("resources");
              }}
            />
          )}
          {view === "traces" && <TracesView ns={ns} onError={setError} />}
          {view === "discovery" && <DiscoveryView ns={ns} onError={setError} />}
          {view === "workflows" && <WorkflowsView ns={ns} onError={setError} />}
          {view === "messaging" && <MessagingView ns={ns} onError={setError} />}
          {view === "secrets" && <SecretsView ns={ns} onError={setError} />}
          {view === "federation" && <FederationView ns={ns} onError={setError} />}
          {view === "compliance" && <ComplianceView onError={setError} />}
        </main>
      </div>

      {cmdOpen && (
        <CommandPalette
          onClose={() => setCmdOpen(false)}
          onSelect={(id) => {
            setView(id);
            setCmdOpen(false);
          }}
        />
      )}
    </div>
  );
}

function CommandPalette({
  onClose,
  onSelect,
}: {
  onClose: () => void;
  onSelect: (id: View) => void;
}) {
  const [q, setQ] = useState("");
  const [idx, setIdx] = useState(0);
  const filtered = useMemo(() => {
    const needle = q.toLowerCase().trim();
    if (!needle) return COMMANDS;
    return COMMANDS.filter(
      (c) => c.label.toLowerCase().includes(needle) || c.hint.toLowerCase().includes(needle),
    );
  }, [q]);

  useEffect(() => setIdx(0), [q]);

  return (
    <div className="cmd-overlay" onClick={onClose}>
      <div className="cmd-modal" onClick={(e) => e.stopPropagation()}>
        <input
          autoFocus
          placeholder="Jump to page, tool, or surface…"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "ArrowDown") {
              e.preventDefault();
              setIdx((i) => Math.min(i + 1, filtered.length - 1));
            } else if (e.key === "ArrowUp") {
              e.preventDefault();
              setIdx((i) => Math.max(i - 1, 0));
            } else if (e.key === "Enter" && filtered[idx]) {
              onSelect(filtered[idx].id);
            }
          }}
        />
        <div className="cmd-results">
          {filtered.map((c, i) => (
            <button
              key={c.id}
              className={i === idx ? "active" : ""}
              onMouseEnter={() => setIdx(i)}
              onClick={() => onSelect(c.id)}
            >
              <span>{c.label}</span>
              <span className="muted mono">{c.hint}</span>
            </button>
          ))}
          {filtered.length === 0 && <p className="muted" style={{ padding: "1rem" }}>No matches</p>}
        </div>
      </div>
    </div>
  );
}

function Overview({
  ns,
  health,
  onError,
  go,
}: {
  ns: string;
  health: Health | null;
  onError: (e: string) => void;
  go: (v: View) => void;
}) {
  const [stats, setStats] = useState({
    resources: 0,
    traces: 0,
    agents: 0,
    packs: 0,
    secrets: 0,
    peers: 0,
  });

  useEffect(() => {
    Promise.all([
      api.listResources(ns),
      api.listTraces(ns),
      api.listAgents(ns),
      api.listCompliance(),
      api.listSecrets(ns).catch(() => ({ secrets: [] as SecretMeta[] })),
      api.listPeers().catch(() => ({ peers: [] as FederatedPeer[] })),
    ])
      .then(([r, t, a, c, s, p]) =>
        setStats({
          resources: r.resources.length,
          traces: t.traces.length,
          agents: a.agents.length,
          packs: c.packs.length,
          secrets: s.secrets.length,
          peers: p.peers.length,
        }),
      )
      .catch((e) => onError(String(e.message ?? e)));
  }, [ns, onError]);

  return (
    <section>
      <header className="page-header">
        <h1>Control plane</h1>
        <p className="muted">
          Configure CRDs, inspect decision memory, manage secrets, and federate AMTP agents
          {health?.federationDomain ? (
            <>
              {" "}
              on <span className="mono">{health.federationDomain}</span>
            </>
          ) : null}
          .
        </p>
      </header>
      <div className="stat-grid">
        <Stat label="Resources" value={stats.resources} onClick={() => go("resources")} />
        <Stat label="Traces" value={stats.traces} onClick={() => go("traces")} />
        <Stat label="Agents" value={stats.agents} onClick={() => go("discovery")} />
        <Stat label="Secrets" value={stats.secrets} onClick={() => go("secrets")} />
        <Stat label="Peers" value={stats.peers} onClick={() => go("federation")} />
        <Stat label="Compliance" value={stats.packs} onClick={() => go("compliance")} />
      </div>
      <PlatformArchitecture />
      <div className="toolbar" style={{ marginTop: "1.25rem" }}>
        <button className="primary" onClick={() => go("maps")}>
          Open flow maps
        </button>
        <button onClick={() => go("workflows")}>Plan a workflow</button>
        <button onClick={() => go("editor")}>Resource editor</button>
      </div>
    </section>
  );
}

function Stat({
  label,
  value,
  onClick,
}: {
  label: string;
  value: number;
  onClick?: () => void;
}) {
  return (
    <button className="stat" onClick={onClick} style={{ cursor: onClick ? "pointer" : "default" }}>
      <div className="stat-value">{value}</div>
      <div className="muted">{label}</div>
    </button>
  );
}

function MapsView({
  ns,
  onError,
  go,
}: {
  ns: string;
  onError: (e: string) => void;
  go: (v: View) => void;
}) {
  const [resources, setResources] = useState<Resource[]>([]);
  const [agents, setAgents] = useState<DiscoveredAgent[]>([]);
  const [messages, setMessages] = useState<PlatformMessage[]>([]);
  const [wfName, setWfName] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([
      api.listResources(ns),
      api.listAgents(ns).catch(() => ({ agents: [] as DiscoveredAgent[] })),
      api.listMessages(ns).catch(() => ({ messages: [] as PlatformMessage[] })),
    ])
      .then(([r, a, m]) => {
        setResources(r.resources);
        setAgents(a.agents);
        setMessages(m.messages);
        const firstWf = r.resources.find((x) => x.kind === "Workflow");
        if (firstWf) setWfName(firstWf.name);
      })
      .catch((e) => onError(String(e.message ?? e)));
  }, [ns, onError]);

  const workflows = resources.filter((r) => r.kind === "Workflow");
  const selectedWf = workflows.find((w) => w.name === wfName) ?? workflows[0];

  return (
    <section>
      <header className="page-header row">
        <div>
          <h1>Flow maps</h1>
          <p className="muted">
            Visual map of how CRDs, agents, workflows, and the message bus connect — read left to
            right.
          </p>
        </div>
        <button className="primary" onClick={() => go("workflows")}>
          Plan a run
        </button>
      </header>

      <div className="map-section">
        <h2>Platform pipeline</h2>
        <p className="muted">End-to-end path from config to runtime memory.</p>
        <PlatformArchitecture />
      </div>

      <div className="map-section">
        <h2>Published workflows</h2>
        <p className="muted">Step order for each Workflow CRD (trigger → agents → approvals).</p>
        {workflows.length === 0 ? (
          <p className="muted">No Workflow resources published yet.</p>
        ) : (
          <>
            <div className="workflow-pick">
              {workflows.map((w) => (
                <button
                  key={w.name}
                  className={selectedWf?.name === w.name ? "active" : ""}
                  onClick={() => setWfName(w.name)}
                >
                  {w.name}
                </button>
              ))}
            </div>
            {selectedWf && (
              <FlowLane
                title={`${selectedWf.name} · v${selectedWf.version}`}
                nodes={workflowToNodes(
                  selectedWf.spec as {
                    trigger?: { type?: string; event?: string };
                    steps?: Array<{
                      id: string;
                      type?: string;
                      ref?: string | null;
                      when?: string | null;
                    }>;
                  },
                )}
              />
            )}
          </>
        )}
      </div>

      <div className="map-section">
        <h2>Agent wiring</h2>
        <p className="muted">Each agent and what it pulls in (model, prompt, tools, guards).</p>
        <AgentGraph resources={resources} />
      </div>

      <div className="map-section">
        <h2>Capability routes</h2>
        <p className="muted">Discovery index — who can handle which capability.</p>
        <DiscoveryMap agents={agents} />
      </div>

      <div className="map-section">
        <h2>Message bus</h2>
        <MessagingGraph messages={messages} />
      </div>
    </section>
  );
}

function ResourcesView({
  ns,
  onError,
  onEdit,
}: {
  ns: string;
  onError: (e: string) => void;
  onEdit?: (r: Resource) => void;
}) {
  const [resources, setResources] = useState<Resource[]>([]);
  const [selected, setSelected] = useState<Resource | null>(null);
  const [filter, setFilter] = useState("");
  const [tab, setTab] = useState<"spec" | "raw">("spec");

  const load = useCallback(() => {
    api
      .listResources(ns)
      .then((r) => {
        setResources(r.resources);
        setSelected((cur) => cur ?? (r.resources[0] ?? null));
      })
      .catch((e) => onError(String(e.message ?? e)));
  }, [ns, onError]);

  useEffect(() => {
    load();
  }, [load]);

  const filtered = resources.filter((r) => {
    const q = filter.toLowerCase().trim();
    if (!q) return true;
    return `${r.kind}/${r.name}`.toLowerCase().includes(q);
  });

  return (
    <section>
      <header className="page-header row">
        <div>
          <h1>Resources</h1>
          <p className="muted">
            Published CRDs in this namespace ({resources.length} total). Open one to inspect —
            edit via Resource editor.
          </p>
        </div>
        <button onClick={load}>Refresh</button>
      </header>
      <div className="form-row">
        <input
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          placeholder="Filter kind/name…"
        />
      </div>
      <div className="split">
        <div className="panel list">
          {filtered.length === 0 && (
            <p className="muted" style={{ padding: "0.75rem" }}>
              No published resources. Run{" "}
              <span className="mono">scripts/seed_offline.py</span> or use Resource editor → Save
              & publish.
            </p>
          )}
          {filtered.map((r) => (
            <button
              key={`${r.kind}/${r.name}`}
              className={
                selected?.name === r.name && selected.kind === r.kind
                  ? "list-item active"
                  : "list-item"
              }
              onClick={() => setSelected(r)}
            >
              <span className="badge">{r.kind}</span>
              <span className="mono">{r.name}</span>
              <span className="muted mono">v{r.version}</span>
            </button>
          ))}
        </div>
        <div className="panel">
          {selected ? (
            <>
              <div className="tabs">
                <button className={tab === "spec" ? "active" : ""} onClick={() => setTab("spec")}>
                  Inspector
                </button>
                <button className={tab === "raw" ? "active" : ""} onClick={() => setTab("raw")}>
                  JSON
                </button>
                {onEdit && (
                  <button
                    className="primary"
                    style={{ marginLeft: "auto" }}
                    onClick={() => onEdit(selected)}
                  >
                    Edit
                  </button>
                )}
              </div>
              {tab === "spec" ? (
                <dl className="inspector-meta">
                  <dt>Kind</dt>
                  <dd>{selected.kind}</dd>
                  <dt>Name</dt>
                  <dd>{selected.name}</dd>
                  <dt>Version</dt>
                  <dd>{selected.version}</dd>
                  <dt>Keys</dt>
                  <dd>{Object.keys(selected.spec).join(", ") || "—"}</dd>
                </dl>
              ) : null}
              {selected.kind === "Workflow" && (
                <FlowLane
                  title="Workflow steps"
                  nodes={workflowToNodes(
                    selected.spec as {
                      trigger?: { type?: string; event?: string };
                      steps?: Array<{
                        id: string;
                        type?: string;
                        ref?: string | null;
                        when?: string | null;
                      }>;
                    },
                  )}
                />
              )}
              {selected.kind === "Agent" && (
                <AgentGraph resources={[selected]} />
              )}
              <pre className="code">{JSON.stringify(selected.spec, null, 2)}</pre>
            </>
          ) : (
            <p className="muted">Select a resource.</p>
          )}
        </div>
      </div>
    </section>
  );
}

function EditorView({
  ns,
  onError,
  onPublished,
  initial,
}: {
  ns: string;
  onError: (e: string) => void;
  onPublished?: () => void;
  initial?: Resource | null;
}) {
  const [kind, setKind] = useState(initial?.kind ?? "Agent");
  const [name, setName] = useState(initial?.name ?? "support-agent");
  const [version, setVersion] = useState(initial?.version ?? "1.0.0");
  const [specText, setSpecText] = useState(
    JSON.stringify(
      initial?.spec ?? {
        role: "executor",
        modelRef: "models/gpt-4o-routed",
        promptRef: "prompts/support-v3",
        toolboxRef: "toolboxes/crm-tools",
      },
      null,
      2,
    ),
  );
  const [existing, setExisting] = useState<Resource[]>([]);
  const [status, setStatus] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api.listResources(ns).then((r) => setExisting(r.resources)).catch(() => undefined);
  }, [ns]);

  useEffect(() => {
    if (!initial) return;
    setKind(initial.kind);
    setName(initial.name);
    setVersion(initial.version);
    setSpecText(JSON.stringify(initial.spec, null, 2));
  }, [initial]);

  function loadExisting(key: string) {
    const r = existing.find((x) => `${x.kind}/${x.name}` === key);
    if (!r) return;
    setKind(r.kind);
    setName(r.name);
    setVersion(r.version);
    setSpecText(JSON.stringify(r.spec, null, 2));
    setStatus(`Loaded ${r.kind}/${r.name}`);
  }

  async function save(publish: boolean) {
    setBusy(true);
    setStatus(null);
    try {
      const spec = JSON.parse(specText) as Record<string, unknown>;
      // Agent CRD does not store capabilities — those live under Discovery
      if ("capabilities" in spec) {
        delete spec.capabilities;
      }
      await api.upsertResource(ns, kind, name, version, spec);
      if (publish) {
        await api.publishResource(ns, kind, name, version);
        setStatus(`Published ${kind}/${name}@${version} — open Resources to see it`);
        onPublished?.();
        const refreshed = await api.listResources(ns);
        setExisting(refreshed.resources);
      } else {
        setStatus(`Saved draft ${kind}/${name}@${version} (not visible in Resources until publish)`);
      }
    } catch (e) {
      onError(String((e as Error).message ?? e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section>
      <header className="page-header">
        <h1>Resource editor</h1>
        <p className="muted">
          Drafts stay here until you <strong>Save & publish</strong>. Published items appear under{" "}
          <strong>Resources</strong>. Agent capabilities are managed in <strong>Discovery</strong>,
          not in the Agent spec.
        </p>
      </header>
      <div className="form-row">
        <select
          value=""
          onChange={(e) => {
            if (e.target.value) loadExisting(e.target.value);
          }}
        >
          <option value="">Load published…</option>
          {existing.map((r) => (
            <option key={`${r.kind}/${r.name}`} value={`${r.kind}/${r.name}`}>
              {r.kind}/{r.name}
            </option>
          ))}
        </select>
        <select value={kind} onChange={(e) => setKind(e.target.value)}>
          {[
            "Agent",
            "Prompt",
            "Tool",
            "Toolbox",
            "ModelRoute",
            "Workflow",
            "KnowledgeSource",
            "Policy",
            "Guardrail",
          ].map((k) => (
            <option key={k} value={k}>
              {k}
            </option>
          ))}
        </select>
        <input value={name} onChange={(e) => setName(e.target.value)} placeholder="name" />
        <input value={version} onChange={(e) => setVersion(e.target.value)} placeholder="version" />
      </div>
      <textarea
        className="code editor"
        value={specText}
        onChange={(e) => setSpecText(e.target.value)}
        spellCheck={false}
      />
      <div className="toolbar" style={{ marginTop: "0.75rem" }}>
        <button className="primary" disabled={busy} onClick={() => save(true)}>
          Save & publish
        </button>
        <button disabled={busy} onClick={() => save(false)}>
          Save draft only
        </button>
        {status && <span className="badge ok">{status}</span>}
      </div>
    </section>
  );
}

function TracesView({ ns, onError }: { ns: string; onError: (e: string) => void }) {
  const [traces, setTraces] = useState<Trace[]>([]);
  const [tags, setTags] = useState("discount");
  const [decision, setDecision] = useState("");
  const [precedents, setPrecedents] = useState<Trace[]>([]);
  const [selected, setSelected] = useState<Trace | null>(null);

  const load = useCallback(() => {
    api
      .listTraces(ns)
      .then((r) => setTraces(r.traces))
      .catch((e) => onError(String(e.message ?? e)));
  }, [ns, onError]);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <section>
      <header className="page-header">
        <h1>Context graph</h1>
        <p className="muted">Decision traces and precedent search.</p>
      </header>
      <div className="form-row">
        <input value={tags} onChange={(e) => setTags(e.target.value)} placeholder="tags" />
        <input
          value={decision}
          onChange={(e) => setDecision(e.target.value)}
          placeholder="decision"
        />
        <button
          className="primary"
          onClick={async () => {
            try {
              await api.createTrace(ns, {
                agent_ref: "agents/support-agent",
                tags: tags.split(",").map((t) => t.trim()).filter(Boolean),
                payload: { decision: decision || "manual_entry" },
                outcome: "recorded",
              });
              setDecision("");
              load();
            } catch (e) {
              onError(String((e as Error).message ?? e));
            }
          }}
        >
          Record
        </button>
        <button
          onClick={async () => {
            try {
              const res = await api.queryPrecedents(ns, {
                tags: tags.split(",").map((t) => t.trim()).filter(Boolean),
                limit: 10,
              });
              setPrecedents(res.precedents);
            } catch (e) {
              onError(String((e as Error).message ?? e));
            }
          }}
        >
          Precedents
        </button>
      </div>
      <div className="split">
        <div className="panel list">
          {traces.map((t) => (
            <button
              key={t.id}
              className={selected?.id === t.id ? "list-item active" : "list-item"}
              onClick={() => setSelected(t)}
            >
              <span className="badge">{t.trace_type}</span>
              <span className="mono">{t.agent_ref}</span>
              <span className="muted">{t.tags.join(", ")}</span>
            </button>
          ))}
        </div>
        <div className="panel">
          {selected ? (
            <pre className="code">{JSON.stringify(selected, null, 2)}</pre>
          ) : precedents.length ? (
            precedents.map((t) => (
              <pre key={t.id} className="code small">
                {JSON.stringify(t.payload, null, 2)}
              </pre>
            ))
          ) : (
            <p className="muted">Select a trace or run a precedent query.</p>
          )}
        </div>
      </div>
    </section>
  );
}

function DiscoveryView({ ns, onError }: { ns: string; onError: (e: string) => void }) {
  const [agents, setAgents] = useState<DiscoveredAgent[]>([]);
  const [cap, setCap] = useState("research");

  const load = useCallback(() => {
    api
      .listAgents(ns)
      .then((r) => setAgents(r.agents))
      .catch((e) => onError(String(e.message ?? e)));
  }, [ns, onError]);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <section>
      <header className="page-header row">
        <div>
          <h1>Discovery</h1>
          <p className="muted">Capability index for routing — who can do what.</p>
        </div>
        <button
          onClick={async () => {
            try {
              await api.syncDiscovery(ns);
              load();
            } catch (e) {
              onError(String((e as Error).message ?? e));
            }
          }}
        >
          Sync bundle
        </button>
      </header>
      <div className="form-row">
        <input value={cap} onChange={(e) => setCap(e.target.value)} />
        <button
          className="primary"
          onClick={async () => {
            try {
              setAgents((await api.discover(ns, [cap])).agents);
            } catch (e) {
              onError(String((e as Error).message ?? e));
            }
          }}
        >
          Find
        </button>
        <button onClick={load}>List all</button>
      </div>
      <DiscoveryMap agents={agents} />
      <div className="panel list" style={{ marginTop: "1rem" }}>
        {agents.map((a) => (
          <div key={a.id} className="list-item static">
            <span className="mono">{a.agent_ref}</span>
            <span className="badge">{a.status}</span>
            <span className="muted">{a.capabilities.join(", ")}</span>
          </div>
        ))}
        {agents.length === 0 && <p className="muted" style={{ padding: "0.75rem" }}>No agents.</p>}
      </div>
    </section>
  );
}

function WorkflowsView({ ns, onError }: { ns: string; onError: (e: string) => void }) {
  const [goal, setGoal] = useState("Research market data across competitors");
  const [plan, setPlan] = useState<{
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
    output: Record<string, unknown>;
  } | null>(null);
  const [busy, setBusy] = useState(false);
  const [published, setPublished] = useState<Resource[]>([]);

  useEffect(() => {
    api
      .listResources(ns)
      .then((r) => setPublished(r.resources.filter((x) => x.kind === "Workflow")))
      .catch(() => undefined);
  }, [ns]);

  return (
    <section>
      <header className="page-header">
        <h1>Dynamic flows</h1>
        <p className="muted">
          Describe a goal — the planner builds a step graph, then runs it. Diagram updates when the
          plan returns.
        </p>
      </header>

      {published.length > 0 && (
        <div className="map-section">
          <h2>Catalog workflows</h2>
          {published.map((w) => (
            <FlowLane
              key={w.name}
              title={w.name}
              nodes={workflowToNodes(
                w.spec as {
                  trigger?: { type?: string; event?: string };
                  steps?: Array<{
                    id: string;
                    type?: string;
                    ref?: string | null;
                    when?: string | null;
                  }>;
                },
              )}
            />
          ))}
        </div>
      )}

      <textarea rows={3} value={goal} onChange={(e) => setGoal(e.target.value)} />
      <div className="toolbar" style={{ marginTop: "0.75rem" }}>
        <button
          className="primary"
          disabled={busy}
          onClick={async () => {
            setBusy(true);
            setPlan(null);
            try {
              setPlan(await api.planWorkflow(ns, goal));
            } catch (e) {
              onError(String((e as Error).message ?? e));
            } finally {
              setBusy(false);
            }
          }}
        >
          {busy ? "Running…" : "Plan & run"}
        </button>
      </div>

      {plan && (
        <>
          <FlowLane
            title={`Plan · ${plan.ir.name} · ${plan.status}`}
            nodes={workflowToNodes({ steps: plan.ir.steps }).map((n) => {
              if (plan.status === "failed" && n.id === String(plan.output?.step ?? "")) {
                return { ...n, status: "failed" };
              }
              if (plan.status === "completed") return { ...n, status: "ok" };
              return n;
            })}
          />
          <pre className="code">{JSON.stringify(plan, null, 2)}</pre>
        </>
      )}
    </section>
  );
}

function MessagingView({ ns, onError }: { ns: string; onError: (e: string) => void }) {
  const [messages, setMessages] = useState<PlatformMessage[]>([]);
  const [recipient, setRecipient] = useState("agents/support-agent");
  const [payload, setPayload] = useState('{"hello":"world"}');
  const [selected, setSelected] = useState<PlatformMessage | null>(null);

  const load = useCallback(() => {
    api
      .listMessages(ns)
      .then((r) => setMessages(r.messages))
      .catch((e) => onError(String(e.message ?? e)));
  }, [ns, onError]);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <section>
      <header className="page-header row">
        <div>
          <h1>Message bus</h1>
          <p className="muted">Agent-to-agent hops on the platform bus.</p>
        </div>
        <button onClick={load}>Refresh</button>
      </header>
      <MessagingGraph messages={messages} />
      <div className="form-row">
        <input value={recipient} onChange={(e) => setRecipient(e.target.value)} />
        <input value={payload} onChange={(e) => setPayload(e.target.value)} />
        <button
          className="primary"
          onClick={async () => {
            try {
              let parsed: Record<string, unknown> = {};
              try {
                parsed = JSON.parse(payload);
              } catch {
                parsed = { text: payload };
              }
              await api.sendMessage(ns, {
                sender: "agents/console",
                recipient,
                subject: "console",
                payload: parsed,
              });
              load();
            } catch (e) {
              onError(String((e as Error).message ?? e));
            }
          }}
        >
          Send
        </button>
      </div>
      <div className="split">
        <div className="panel list">
          {messages.map((m) => (
            <button
              key={m.id}
              className={selected?.id === m.id ? "list-item active" : "list-item"}
              onClick={() => setSelected(m)}
            >
              <span className="badge">{m.status}</span>
              <span className="mono">
                {m.sender} → {m.recipient}
              </span>
            </button>
          ))}
        </div>
        <div className="panel">
          {selected ? (
            <pre className="code">{JSON.stringify(selected, null, 2)}</pre>
          ) : (
            <p className="muted">Select a message.</p>
          )}
        </div>
      </div>
    </section>
  );
}

function SecretsView({ ns, onError }: { ns: string; onError: (e: string) => void }) {
  const [secrets, setSecrets] = useState<SecretMeta[]>([]);
  const [name, setName] = useState("openai-key");
  const [value, setValue] = useState("");

  const load = useCallback(() => {
    api
      .listSecrets(ns)
      .then((r) => setSecrets(r.secrets))
      .catch((e) => onError(String(e.message ?? e)));
  }, [ns, onError]);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <section>
      <header className="page-header">
        <h1>Secrets</h1>
        <p className="muted">
          Encrypted at rest. Reference as <span className="mono">secrets/name</span> from tools.
        </p>
      </header>
      <div className="form-row">
        <input value={name} onChange={(e) => setName(e.target.value)} />
        <input
          type="password"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          placeholder="value"
        />
        <button
          className="primary"
          disabled={!value}
          onClick={async () => {
            try {
              await api.putSecret(ns, name, value);
              setValue("");
              load();
            } catch (e) {
              onError(String((e as Error).message ?? e));
            }
          }}
        >
          Store
        </button>
      </div>
      <div className="panel list">
        {secrets.map((s) => (
          <div key={s.id} className="list-item static">
            <span className="mono">{s.name}</span>
            <span className="muted mono">{s.created_at}</span>
            <button
              className="danger"
              onClick={async () => {
                try {
                  await api.deleteSecret(ns, s.name);
                  load();
                } catch (e) {
                  onError(String((e as Error).message ?? e));
                }
              }}
            >
              Delete
            </button>
          </div>
        ))}
      </div>
    </section>
  );
}

function FederationView({ ns, onError }: { ns: string; onError: (e: string) => void }) {
  const [info, setInfo] = useState<Record<string, unknown> | null>(null);
  const [peers, setPeers] = useState<FederatedPeer[]>([]);
  const [agents, setAgents] = useState<Array<Record<string, unknown>>>([]);
  const [dns, setDns] = useState<{ name: string; value: string } | null>(null);
  const [domain, setDomain] = useState("peer.example");
  const [gateway, setGateway] = useState("http://localhost:8081");
  const [recipient, setRecipient] = useState("support@local.ai-platform");
  const [result, setResult] = useState<string | null>(null);
  const [tab, setTab] = useState<"send" | "dns" | "agents">("send");

  const load = useCallback(() => {
    Promise.all([
      api.federationInfo(),
      api.listPeers(),
      api.amtpCapabilities().catch(() => null),
      api.amtpAgents().catch(() => ({ agents: [] })),
      api.amtpDnsTxt("http://localhost:8080").catch(() => null),
    ])
      .then(([i, p, caps, a, d]) => {
        setInfo({ ...(i as object), capabilities: caps });
        setPeers(p.peers);
        setAgents(a.agents);
        setDns(d);
      })
      .catch((e) => onError(String(e.message ?? e)));
  }, [onError]);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <section>
      <header className="page-header">
        <h1>AMTP federation</h1>
        <p className="muted">
          Domain{" "}
          <span className="mono">{String((info as { domain?: string } | null)?.domain ?? "…")}</span>
          — DNS discovery, fan-out send, schemas, agent directory.
        </p>
      </header>
      <div className="tabs">
        <button className={tab === "send" ? "active" : ""} onClick={() => setTab("send")}>
          Send
        </button>
        <button className={tab === "agents" ? "active" : ""} onClick={() => setTab("agents")}>
          Agents
        </button>
        <button className={tab === "dns" ? "active" : ""} onClick={() => setTab("dns")}>
          DNS TXT
        </button>
      </div>

      {tab === "send" && (
        <>
          <div className="form-row">
            <input value={domain} onChange={(e) => setDomain(e.target.value)} placeholder="peer domain" />
            <input value={gateway} onChange={(e) => setGateway(e.target.value)} placeholder="gateway" />
            <button
              className="primary"
              onClick={async () => {
                try {
                  await api.registerPeer({ domain, gateway });
                  load();
                } catch (e) {
                  onError(String((e as Error).message ?? e));
                }
              }}
            >
              Register peer
            </button>
          </div>
          <div className="form-row">
            <input
              value={recipient}
              onChange={(e) => setRecipient(e.target.value)}
              placeholder="agent@domain"
            />
            <button
              onClick={async () => {
                try {
                  const res = await api.amtpSend({
                    version: "1.0",
                    sender: "console@local.ai-platform",
                    recipients: [recipient],
                    subject: "console-ping",
                    payload: { hello: true, via: "amtp" },
                  });
                  setResult(JSON.stringify(res, null, 2));
                } catch (e) {
                  onError(String((e as Error).message ?? e));
                }
              }}
            >
              AMTP send
            </button>
            <button
              onClick={async () => {
                try {
                  const res = await api.federationSend(ns, {
                    sender: "agents/console",
                    recipient: "agents/support-agent",
                    payload: { via: "local-bus" },
                  });
                  setResult(JSON.stringify(res, null, 2));
                } catch (e) {
                  onError(String((e as Error).message ?? e));
                }
              }}
            >
              Local hop
            </button>
          </div>
          <div className="panel list">
            <h3>Peers</h3>
            {peers.map((p) => (
              <div key={p.domain} className="list-item static">
                <span className="mono">{p.domain}</span>
                <span className="muted mono">{p.gateway}</span>
              </div>
            ))}
            {peers.length === 0 && <p className="muted" style={{ padding: "0.75rem" }}>No peers.</p>}
          </div>
        </>
      )}

      {tab === "agents" && (
        <div className="panel list">
          {agents.map((a, i) => (
            <div key={i} className="list-item static">
              <span className="mono">{String(a.address)}</span>
              <span className="badge">{String(a.deliveryMode)}</span>
            </div>
          ))}
          {agents.length === 0 && (
            <p className="muted" style={{ padding: "0.75rem" }}>
              Register agents via <span className="mono">POST /v1/admin/agents</span>.
            </p>
          )}
        </div>
      )}

      {tab === "dns" && dns && (
        <div className="panel">
          <dl className="inspector-meta">
            <dt>Name</dt>
            <dd>{dns.name}</dd>
            <dt>Type</dt>
            <dd>TXT</dd>
          </dl>
          <pre className="code">{dns.value}</pre>
        </div>
      )}

      {result && <pre className="code" style={{ marginTop: "1rem" }}>{result}</pre>}
    </section>
  );
}

function ComplianceView({ onError }: { onError: (e: string) => void }) {
  const [packs, setPacks] = useState<CompliancePack[]>([]);
  useEffect(() => {
    api
      .listCompliance()
      .then((r) => setPacks(r.packs))
      .catch((e) => onError(String(e.message ?? e)));
  }, [onError]);

  return (
    <section>
      <header className="page-header">
        <h1>Compliance packs</h1>
        <p className="muted">HIPAA, PCI, GDPR, SOC2 bundles.</p>
      </header>
      <div className="pack-grid">
        {packs.map((p) => (
          <div key={p.id} className="pack">
            <div className="badge">{p.framework}</div>
            <h3>{p.name}</h3>
            <p className="muted">{p.description}</p>
            <span className="mono muted">
              {p.id} · v{p.version}
            </span>
          </div>
        ))}
      </div>
    </section>
  );
}
