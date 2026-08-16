import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";
import {
  api,
  clearSession,
  DEFAULT_NS,
  denialFromExecution,
  formatError,
  getNamespace,
  getRecentNamespaces,
  getToken,
  getUser,
  setNamespace,
  setSession,
  type AuthUser,
  type CompliancePack,
  type DiscoveredAgent,
  type EdgeNode,
  type FederatedPeer,
  type Health,
  type HitlInboxItem,
  type MarketplacePlugin,
  type MetricStats,
  type NamespaceInfo,
  type PlatformMessage,
  type PolicyDenial,
  type RegionInfo,
  type Resource,
  type SecretMeta,
  type Trace,
  type AuditEvent,
  type ScimUser,
} from "./api";
import {
  AgentGraph,
  collaborationNodes,
  collaborationRoles,
  DiscoveryMap,
  FlowLane,
  MessagingGraph,
  PlatformArchitecture,
  workflowToNodes,
} from "./diagrams";
import {
  cleanSpec,
  defaultSpec,
  hasVisualForm,
  ResourceSpecForm,
  type Spec,
} from "./resourceForms";
import "./styles.css";

type View =
  | "overview"
  | "maps"
  | "resources"
  | "editor"
  | "traces"
  | "discovery"
  | "workflows"
  | "collaboration"
  | "messaging"
  | "secrets"
  | "federation"
  | "compliance"
  | "promotion"
  | "marketplace"
  | "metrics"
  | "evaluations"
  | "git"
  | "terraform"
  | "regions"
  | "hitl"
  | "activity"
  | "identity";

const NAV_GROUPS: { label: string; items: { id: View; label: string }[] }[] = [
  {
    label: "Build",
    items: [
      { id: "overview", label: "Overview" },
      { id: "maps", label: "Flow maps" },
      { id: "resources", label: "Resources" },
      { id: "editor", label: "Resource editor" },
      { id: "workflows", label: "Dynamic flows" },
      { id: "collaboration", label: "Multi-agent" },
    ],
  },
  {
    label: "Runtime",
    items: [
      { id: "traces", label: "Context graph" },
      { id: "discovery", label: "Discovery" },
      { id: "messaging", label: "Message bus" },
      { id: "hitl", label: "HITL inbox" },
      { id: "federation", label: "AMTP federation" },
    ],
  },
  {
    label: "Ops",
    items: [
      { id: "activity", label: "Activity" },
      { id: "identity", label: "Identity (SCIM)" },
      { id: "metrics", label: "Metrics" },
      { id: "evaluations", label: "Evaluations" },
      { id: "regions", label: "Regions & edge" },
      { id: "git", label: "Git sync" },
      { id: "terraform", label: "Terraform" },
      { id: "secrets", label: "Secrets" },
      { id: "compliance", label: "Compliance" },
      { id: "promotion", label: "Promotion" },
      { id: "marketplace", label: "Marketplace" },
    ],
  },
];

const COMMANDS: { id: View; label: string; hint: string }[] = NAV_GROUPS.flatMap((g) =>
  g.items.map((i) => ({ id: i.id, label: i.label, hint: g.label })),
);

function PolicyDenialCard({ denial }: { denial: PolicyDenial }) {
  return (
    <div className="panel policy-denial" style={{ marginBottom: "1rem", borderColor: "var(--danger, #b33)" }}>
      <div className="form-section-title">Why this was denied</div>
      <div className="diagnosis-card">
        <div>
          <span className="badge danger">policy denied</span>
          {denial.action ? <span className="mono"> · {denial.action}</span> : null}
          {denial.resource ? <span className="muted mono"> · {denial.resource}</span> : null}
        </div>
        {denial.reason ? (
          <div>
            <strong>Reason</strong> · <span className="mono">{denial.reason}</span>
          </div>
        ) : null}
        {denial.matchedRule ? (
          <div>
            <strong>Matched rule</strong> · <span className="mono">{denial.matchedRule}</span>
          </div>
        ) : null}
        {denial.gate ? (
          <div>
            <strong>Gate</strong> · <span className="mono">{denial.gate}</span>
          </div>
        ) : null}
        {denial.diagnosis ? <p className="muted">{denial.diagnosis}</p> : null}
        <p className="muted form-hint">
          Publish a Policy that allows this principal/action, or remove the deny rule matching above.
        </p>
      </div>
    </div>
  );
}

export default function App() {
  const [view, setView] = useState<View>("overview");
  const [ns, setNs] = useState(() => getNamespace());
  const [namespaces, setNamespaces] = useState<NamespaceInfo[]>([]);
  const [nsDraft, setNsDraft] = useState(() => getNamespace());
  const [health, setHealth] = useState<Health | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [cmdOpen, setCmdOpen] = useState(false);
  const [editTarget, setEditTarget] = useState<Resource | null>(null);
  const [editorSeed, setEditorSeed] = useState<"new" | "clone" | null>(null);
  const [user, setUser] = useState<AuthUser | null>(() => getUser());
  const [authed, setAuthed] = useState(() => Boolean(getToken()));

  useEffect(() => {
    if (!authed) return;
    api.health().then(setHealth).catch(() => setHealth(null));
    api
      .listNamespaces()
      .then((r) => setNamespaces(r.namespaces))
      .catch(() => undefined);
  }, [authed, ns]);

  function switchNamespace(path: string) {
    const clean = path.trim();
    if (!clean || !clean.includes("/")) {
      setError("Namespace must be org/project");
      return;
    }
    void api
      .ensureNamespace(clean)
      .then(() => {
        setNamespace(clean);
        setNs(clean);
        setNsDraft(clean);
        setEditTarget(null);
        api.listNamespaces().then((r) => setNamespaces(r.namespaces)).catch(() => undefined);
      })
      .catch((e) => setError(String((e as Error).message ?? e)));
  }

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

  if (!authed) {
    return (
      <LoginScreen
        onLoggedIn={(u) => {
          setUser(u);
          setAuthed(true);
        }}
        onError={setError}
        error={error}
      />
    );
  }

  return (
    <div className="app-root" data-testid="studio-app">
      <header className="topbar">
        <div className="topbar-brand">
          <span className="topbar-mark">AI</span>
          <span>Platform Studio</span>
        </div>
        <button className="cmd-btn" onClick={() => setCmdOpen(true)}>
          ⌘K Command
        </button>
        <div className="topbar-ns muted">
          <label className="ns-switcher" data-testid="ns-switcher">
            <span className="muted">NS</span>
            <select
              value={ns}
              onChange={(e) => switchNamespace(e.target.value)}
              title="Active namespace"
            >
              {Array.from(
                new Set([
                  ns,
                  DEFAULT_NS,
                  ...getRecentNamespaces(),
                  ...namespaces.map((n) => n.path),
                ]),
              ).map((path) => (
                <option key={path} value={path}>
                  {path}
                </option>
              ))}
            </select>
          </label>
          <form
            className="ns-add"
            onSubmit={(e) => {
              e.preventDefault();
              switchNamespace(nsDraft);
            }}
          >
            <input
              value={nsDraft}
              onChange={(e) => setNsDraft(e.target.value)}
              placeholder="org/project"
              aria-label="Namespace path"
            />
            <button type="submit" className="ghost">
              Go
            </button>
          </form>
          <span className="badge ok" data-testid="health-version">
            {health ? `v${health.version}` : "…"}
          </span>
          <span className="badge">{health?.sqlBackend ?? health?.registryBackend ?? "—"}</span>
          <span className="mono" data-testid="user-email">
            {user?.email ?? "signed in"}
          </span>
          <button
            className="ghost"
            data-testid="sign-out"
            onClick={() => {
              clearSession();
              setAuthed(false);
              setUser(null);
            }}
          >
            Sign out
          </button>
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
                  data-testid={`nav-${item.id}`}
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
                setEditorSeed(null);
                setEditTarget(r);
                setView("editor");
              }}
              onNew={() => {
                setEditorSeed("new");
                setEditTarget(null);
                setView("editor");
              }}
              onClone={(r) => {
                setEditorSeed("clone");
                setEditTarget({
                  ...r,
                  name: `${r.name}-copy`,
                  version: "1.0.0",
                });
                setView("editor");
              }}
            />
          )}
          {view === "editor" && (
            <EditorView
              ns={ns}
              onError={setError}
              initial={editTarget}
              seed={editorSeed}
              onPublished={() => {
                setEditorSeed(null);
                setEditTarget(null);
                setView("resources");
              }}
            />
          )}
          {view === "traces" && <TracesView ns={ns} onError={setError} />}
          {view === "discovery" && <DiscoveryView ns={ns} onError={setError} />}
          {view === "workflows" && <WorkflowsView ns={ns} onError={setError} go={setView} />}
          {view === "collaboration" && <CollaborationView ns={ns} onError={setError} />}
          {view === "messaging" && <MessagingView ns={ns} onError={setError} />}
          {view === "hitl" && <HitlInboxView ns={ns} onError={setError} />}
          {view === "activity" && <ActivityView ns={ns} onError={setError} />}
          {view === "identity" && <IdentityView ns={ns} onError={setError} />}
          {view === "secrets" && <SecretsView ns={ns} onError={setError} />}
          {view === "federation" && <FederationView ns={ns} onError={setError} />}
          {view === "compliance" && <ComplianceView ns={ns} user={user} onError={setError} />}
          {view === "promotion" && <PromotionView ns={ns} user={user} onError={setError} />}
          {view === "marketplace" && <MarketplaceView ns={ns} onError={setError} />}
          {view === "metrics" && <MetricsView ns={ns} onError={setError} />}
          {view === "evaluations" && <EvaluationsView ns={ns} onError={setError} />}
          {view === "regions" && <RegionsView ns={ns} onError={setError} />}
          {view === "git" && <GitSyncView ns={ns} user={user} onError={setError} />}
          {view === "terraform" && <TerraformView ns={ns} onError={setError} />}
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

function b64url(bytes: ArrayBuffer | Uint8Array): string {
  const arr = bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes);
  let s = "";
  arr.forEach((b) => {
    s += String.fromCharCode(b);
  });
  return btoa(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

async function createPkce(): Promise<{ verifier: string; challenge: string }> {
  const raw = crypto.getRandomValues(new Uint8Array(32));
  const verifier = b64url(raw);
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(verifier));
  return { verifier, challenge: b64url(digest) };
}

function LoginScreen({
  onLoggedIn,
  onError,
  error,
}: {
  onLoggedIn: (u: AuthUser) => void;
  onError: (e: string) => void;
  error: string | null;
}) {
  const [email, setEmail] = useState("ops@example.com");
  const [orgId, setOrgId] = useState("default-org");
  const [busy, setBusy] = useState(false);
  const [mode, setMode] = useState<"dev" | "oidc">("dev");
  const [devLoginEnabled, setDevLoginEnabled] = useState(true);
  const [idpLabel, setIdpLabel] = useState("OIDC");

  useEffect(() => {
    api
      .authConfig()
      .then((cfg) => {
        setMode(cfg.mode);
        setDevLoginEnabled(cfg.devLoginEnabled);
        setOrgId(cfg.defaultOrgId || "default-org");
        if (cfg.oidc?.issuer) {
          if (cfg.oidc.issuer.includes("okta.com")) setIdpLabel("Okta");
          else if (cfg.oidc.issuer.includes("microsoftonline.com") || cfg.oidc.issuer.includes("windows.net"))
            setIdpLabel("Microsoft");
          else if (cfg.oidc.issuer.includes("auth0.com")) setIdpLabel("Auth0");
          else setIdpLabel("OIDC IdP");
        }
      })
      .catch(() => undefined);
  }, []);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const code = params.get("code");
    const state = params.get("state");
    if (!code || !state) return;

    const pendingRaw = sessionStorage.getItem("platform.oidc.pending");
    if (!pendingRaw) {
      onError("OIDC callback missing PKCE session — start sign-in again");
      return;
    }
    let pending: { verifier: string; state: string; orgId: string };
    try {
      pending = JSON.parse(pendingRaw) as { verifier: string; state: string; orgId: string };
    } catch {
      onError("OIDC session corrupt — start sign-in again");
      return;
    }
    if (pending.state !== state) {
      onError("OIDC state mismatch — start sign-in again");
      return;
    }

    setBusy(true);
    void api
      .oidcCallback({
        code,
        state,
        codeVerifier: pending.verifier,
        orgId: pending.orgId,
      })
      .then((res) => {
        sessionStorage.removeItem("platform.oidc.pending");
        window.history.replaceState({}, "", window.location.pathname);
        setSession(res.accessToken, res.user);
        onLoggedIn(res.user);
      })
      .catch((err) => onError(String((err as Error).message ?? err)))
      .finally(() => setBusy(false));
  }, [onError, onLoggedIn]);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    try {
      const res = await api.login(email, orgId, email.split("@")[0]);
      setSession(res.accessToken, res.user);
      onLoggedIn(res.user);
    } catch (err) {
      onError(String((err as Error).message ?? err));
    } finally {
      setBusy(false);
    }
  }

  async function startOidc() {
    setBusy(true);
    try {
      const pkce = await createPkce();
      const redirectUri = `${window.location.origin}/`;
      const started = await api.oidcStart({
        codeChallenge: pkce.challenge,
        orgId,
        redirectUri,
      });
      sessionStorage.setItem(
        "platform.oidc.pending",
        JSON.stringify({ verifier: pkce.verifier, state: started.state, orgId }),
      );
      window.location.assign(started.authorizationUrl);
    } catch (err) {
      onError(String((err as Error).message ?? err));
      setBusy(false);
    }
  }

  return (
    <div className="login-screen" data-testid="login-screen">
      <form className="login-card" onSubmit={submit} data-testid="login-form">
        <div className="topbar-brand" style={{ marginBottom: "1rem" }}>
          <span className="topbar-mark">AI</span>
          <span>Platform Studio</span>
        </div>
        <h1>Sign in</h1>
        <p className="muted">
          {mode === "oidc"
            ? `Enterprise OIDC via ${idpLabel}. Platform issues a session JWT after IdP login.`
            : "Dev JWT login for the control plane. Creates the user on first sign-in."}
        </p>
        {error && <div className="banner error">{error}</div>}

        {mode === "oidc" && (
          <button
            type="button"
            className="primary"
            style={{ width: "100%", marginBottom: "0.85rem" }}
            disabled={busy}
            data-testid="oidc-login"
            onClick={() => void startOidc()}
          >
            {busy ? "Redirecting…" : `Sign in with ${idpLabel}`}
          </button>
        )}

        {devLoginEnabled && (
          <>
            {mode === "oidc" && (
              <p className="muted form-hint" style={{ marginBottom: "0.75rem" }}>
                Dev email login is still enabled (set <span className="mono">PLATFORM_ALLOW_DEV_LOGIN=false</span>{" "}
                in production).
              </p>
            )}
            <label className="form-field">
              <span className="form-label">Email</span>
              <input
                data-testid="login-email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                required
              />
            </label>
            <label className="form-field">
              <span className="form-label">Org id</span>
              <input
                data-testid="login-org"
                value={orgId}
                onChange={(e) => setOrgId(e.target.value)}
                required
              />
            </label>
            <button className="primary" disabled={busy} type="submit" data-testid="login-submit">
              {busy ? "Signing in…" : mode === "oidc" ? "Dev sign in" : "Sign in"}
            </button>
          </>
        )}

        {!devLoginEnabled && mode === "oidc" && (
          <label className="form-field">
            <span className="form-label">Org id (claim mapping)</span>
            <input
              data-testid="login-org"
              value={orgId}
              onChange={(e) => setOrgId(e.target.value)}
              required
            />
          </label>
        )}
      </form>
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
  onNew,
  onClone,
}: {
  ns: string;
  onError: (e: string) => void;
  onEdit?: (r: Resource) => void;
  onNew?: () => void;
  onClone?: (r: Resource) => void;
}) {
  const [resources, setResources] = useState<Resource[]>([]);
  const [selected, setSelected] = useState<Resource | null>(null);
  const [filter, setFilter] = useState("");
  const [tab, setTab] = useState<"spec" | "raw">("spec");
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    api
      .listResources(ns)
      .then((r) => {
        setResources(r.resources);
        setSelected((cur) => {
          if (!cur) return r.resources[0] ?? null;
          return (
            r.resources.find((x) => x.kind === cur.kind && x.name === cur.name) ??
            r.resources[0] ??
            null
          );
        });
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

  async function unpublishSelected() {
    if (!selected) return;
    setBusy(true);
    try {
      await api.unpublishResource(ns, selected.kind, selected.name);
      load();
    } catch (e) {
      onError(String((e as Error).message ?? e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section>
      <header className="page-header row">
        <div>
          <h1>Resources</h1>
          <p className="muted">
            Published CRDs in <span className="mono">{ns}</span> ({resources.length} total). Create,
            clone, edit, or unpublish.
          </p>
        </div>
        <div className="form-row" style={{ marginBottom: 0 }}>
          {onNew && (
            <button className="primary" onClick={onNew}>
              New resource
            </button>
          )}
          <button onClick={load}>Refresh</button>
        </div>
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
              No published resources. Use <strong>New resource</strong>, Git sync, or{" "}
              <span className="mono">scripts/seed_offline.py</span>.
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
                {onClone && (
                  <button onClick={() => onClone(selected)}>Clone</button>
                )}
                <button disabled={busy} onClick={unpublishSelected}>
                  {busy ? "…" : "Unpublish"}
                </button>
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
  seed,
}: {
  ns: string;
  onError: (e: string) => void;
  onPublished?: () => void;
  initial?: Resource | null;
  seed?: "new" | "clone" | null;
}) {
  const [kind, setKind] = useState(initial?.kind ?? "Agent");
  const [name, setName] = useState(initial?.name ?? "support-agent");
  const [version, setVersion] = useState(initial?.version ?? "1.0.0");
  const [spec, setSpec] = useState<Spec>(
    () => (initial?.spec as Spec) ?? defaultSpec(initial?.kind ?? "Agent"),
  );
  const [editMode, setEditMode] = useState<"form" | "json">("form");
  const [specText, setSpecText] = useState(() =>
    JSON.stringify(initial?.spec ?? defaultSpec(initial?.kind ?? "Agent"), null, 2),
  );
  const [existing, setExisting] = useState<Resource[]>([]);
  const [status, setStatus] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [testInput, setTestInput] = useState('{"message":"Hello from Platform Studio"}');
  const [testResult, setTestResult] = useState<Record<string, unknown> | null>(null);
  const [testBusy, setTestBusy] = useState(false);
  const [evalSuiteRef, setEvalSuiteRef] = useState("");
  const [evalResult, setEvalResult] = useState<Record<string, unknown> | null>(null);
  const [evalBusy, setEvalBusy] = useState(false);
  const [evalTarget, setEvalTarget] = useState("agents/support-agent");

  useEffect(() => {
    api.listResources(ns).then((r) => setExisting(r.resources)).catch(() => undefined);
  }, [ns]);

  useEffect(() => {
    if (seed === "new") {
      const nextKind = "Agent";
      const nextSpec = defaultSpec(nextKind);
      setKind(nextKind);
      setName("new-agent");
      setVersion("1.0.0");
      setSpec(nextSpec);
      setSpecText(JSON.stringify(nextSpec, null, 2));
      setEditMode("form");
      setStatus("New draft — set kind/name, then Save & publish");
      return;
    }
    if (!initial) return;
    setKind(initial.kind);
    setName(initial.name);
    setVersion(initial.version);
    setSpec(initial.spec as Spec);
    setSpecText(JSON.stringify(initial.spec, null, 2));
    setEditMode(hasVisualForm(initial.kind) ? "form" : "json");
    if (seed === "clone") {
      setStatus(`Cloned as ${initial.kind}/${initial.name} — Save & publish to create`);
    }
  }, [initial, seed]);

  function loadExisting(key: string) {
    const r = existing.find((x) => `${x.kind}/${x.name}` === key);
    if (!r) return;
    setKind(r.kind);
    setName(r.name);
    setVersion(r.version);
    setSpec(r.spec as Spec);
    setSpecText(JSON.stringify(r.spec, null, 2));
    setEditMode(hasVisualForm(r.kind) ? "form" : "json");
    setStatus(`Loaded ${r.kind}/${r.name}`);
  }

  function onKindChange(next: string) {
    setKind(next);
    const nextSpec = defaultSpec(next);
    setSpec(nextSpec);
    setSpecText(JSON.stringify(nextSpec, null, 2));
    setEditMode(hasVisualForm(next) ? "form" : "json");
  }

  function switchToJson() {
    setSpecText(JSON.stringify(cleanSpec(spec), null, 2));
    setEditMode("json");
  }

  function switchToForm() {
    if (!hasVisualForm(kind)) return;
    try {
      setSpec(JSON.parse(specText) as Spec);
    } catch {
      onError("Fix JSON syntax before switching to Form");
      return;
    }
    setEditMode("form");
  }

  function onFormSpecChange(next: Spec) {
    setSpec(next);
    setSpecText(JSON.stringify(cleanSpec(next), null, 2));
  }

  async function save(publish: boolean) {
    setBusy(true);
    setStatus(null);
    try {
      let finalSpec: Spec;
      if (editMode === "json") {
        finalSpec = cleanSpec(JSON.parse(specText) as Spec);
      } else {
        finalSpec = cleanSpec(spec);
      }
      await api.upsertResource(ns, kind, name, version, finalSpec);
      if (publish) {
        await api.publishResource(
          ns,
          kind,
          name,
          version,
          kind === "Agent" && evalSuiteRef.trim() ? evalSuiteRef.trim() : undefined,
        );
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

  async function testAgent() {
    setTestBusy(true);
    setTestResult(null);
    try {
      const input = JSON.parse(testInput) as Record<string, unknown>;
      const result = await api.runResource(ns, `agents/${name}`, input);
      setTestResult(result as unknown as Record<string, unknown>);
    } catch (e) {
      onError(
        `${String((e as Error).message ?? e)}. Save & publish this agent before testing it.`,
      );
    } finally {
      setTestBusy(false);
    }
  }

  async function runSuiteEval() {
    setEvalBusy(true);
    setEvalResult(null);
    try {
      // Persist draft so the runner can load suiteVersion if not yet published.
      let finalSpec: Spec;
      if (editMode === "json") {
        finalSpec = cleanSpec(JSON.parse(specText) as Spec);
      } else {
        finalSpec = cleanSpec(spec);
      }
      await api.upsertResource(ns, kind, name, version, finalSpec);
      const result = await api.runEvaluation(ns, {
        suiteRef: `evaluationsuites/${name}`,
        targetRef: evalTarget.trim() || "agents/support-agent",
        targetVersion: version,
        suiteVersion: version,
      });
      setEvalResult(result as unknown as Record<string, unknown>);
      setStatus(
        result.passed
          ? `Eval passed (overall ${result.overall.toFixed(2)})`
          : `Eval failed: ${result.gateReason ?? "gate"}`,
      );
    } catch (e) {
      onError(String((e as Error).message ?? e));
    } finally {
      setEvalBusy(false);
    }
  }

  return (
    <section>
      <header className="page-header">
        <h1>Resource editor</h1>
        <p className="muted">
          Use the <strong>Form</strong> tab to configure visually — generated JSON appears below.
          Switch to <strong>JSON</strong> for advanced edits. Then <strong>Save & publish</strong>.
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
        <select value={kind} onChange={(e) => onKindChange(e.target.value)}>
          {[
            "Agent",
            "Prompt",
            "Tool",
            "Toolbox",
            "ModelRoute",
            "Workflow",
            "KnowledgeSource",
            "MemoryProfile",
            "Environment",
            "EvaluationSuite",
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

      <div className="tabs editor-tabs">
        <button
          className={editMode === "form" ? "active" : ""}
          disabled={!hasVisualForm(kind)}
          onClick={switchToForm}
        >
          Form
        </button>
        <button className={editMode === "json" ? "active" : ""} onClick={switchToJson}>
          JSON
        </button>
      </div>

      <div className="editor-split">
        <div className="panel editor-form-panel">
          {editMode === "form" ? (
            <ResourceSpecForm
              kind={kind}
              spec={spec}
              onChange={onFormSpecChange}
              resources={existing}
            />
          ) : (
            <textarea
              className="code editor"
              value={specText}
              onChange={(e) => setSpecText(e.target.value)}
              spellCheck={false}
            />
          )}
        </div>
        <div className="panel editor-preview-panel">
          <div className="form-section-title">Generated spec (preview)</div>
          <p className="muted form-hint">
            This is what gets saved when you publish — same as YAML without the metadata wrapper.
          </p>
          <pre className="code small preview-json">
            {editMode === "json"
              ? specText
              : JSON.stringify(cleanSpec(spec), null, 2)}
          </pre>
          <div className="form-section-title" style={{ marginTop: "1rem" }}>
            Full CRD document
          </div>
          <pre className="code small preview-json">
            {(() => {
              try {
                const s =
                  editMode === "json"
                    ? (JSON.parse(specText || "{}") as Spec)
                    : cleanSpec(spec);
                return JSON.stringify(
                  {
                    apiVersion: "platform.ai/v1",
                    kind,
                    metadata: { name, namespace: ns, version },
                    spec: s,
                  },
                  null,
                  2,
                );
              } catch {
                return "// fix JSON syntax to preview full document";
              }
            })()}
          </pre>
        </div>
      </div>

      <div className="toolbar" style={{ marginTop: "0.75rem" }}>
        <button className="primary" disabled={busy} onClick={() => save(true)}>
          Save & publish
        </button>
        <button disabled={busy} onClick={() => save(false)}>
          Save draft only
        </button>
        {kind === "Agent" && (
          <button disabled={testBusy} onClick={testAgent}>
            {testBusy ? "Testing…" : "Test published agent"}
          </button>
        )}
        {kind === "EvaluationSuite" && (
          <button disabled={evalBusy} onClick={runSuiteEval}>
            {evalBusy ? "Evaluating…" : "Run evaluation"}
          </button>
        )}
        {status && <span className="badge ok">{status}</span>}
      </div>
      {kind === "Agent" && (
        <div className="form-row" style={{ marginTop: "0.5rem" }}>
          <label className="muted">
            Optional eval suite on publish{" "}
            <input
              value={evalSuiteRef}
              onChange={(e) => setEvalSuiteRef(e.target.value)}
              placeholder="evaluationsuites/support-quality (or leave blank for triggers)"
              style={{ minWidth: "22rem" }}
            />
          </label>
        </div>
      )}
      {kind === "Agent" && (
        <div className="panel test-agent-panel">
          <div>
            <div className="form-section-title">Test input</div>
            <p className="muted form-hint">
              Runs the currently published <span className="mono">agents/{name}</span>.
            </p>
            <textarea
              className="code editor test-input"
              value={testInput}
              onChange={(e) => setTestInput(e.target.value)}
              spellCheck={false}
            />
          </div>
          <div>
            <div className="form-section-title">Run result</div>
            <pre className="code test-result">
              {testResult
                ? JSON.stringify(testResult, null, 2)
                : "Run the published agent to see tokens, tool calls, or errors."}
            </pre>
          </div>
        </div>
      )}
      {kind === "EvaluationSuite" && (
        <div className="panel test-agent-panel">
          <div>
            <div className="form-section-title">Eval target</div>
            <p className="muted form-hint">
              Runs judges against a published agent (or the draft suite cases offline).
            </p>
            <input
              value={evalTarget}
              onChange={(e) => setEvalTarget(e.target.value)}
              placeholder="agents/support-agent"
            />
          </div>
          <div>
            <div className="form-section-title">Eval result</div>
            <pre className="code test-result">
              {evalResult
                ? JSON.stringify(evalResult, null, 2)
                : "Run evaluation to see scores, gate reason, and per-case judge details."}
            </pre>
          </div>
        </div>
      )}
    </section>
  );
}

function CollaborationView({ ns, onError }: { ns: string; onError: (e: string) => void }) {
  const [resources, setResources] = useState<Resource[]>([]);
  const [agentRef, setAgentRef] = useState("agents/multi-support-agent");
  const [pattern, setPattern] = useState("planner_executor_reviewer");
  const [wiring, setWiring] = useState<Record<string, string>>({});
  const [maxIterations, setMaxIterations] = useState(2);
  const [input, setInput] = useState('{"message":"Plan then answer a billing refund request"}');
  const [result, setResult] = useState<Record<string, unknown> | null>(null);
  const [liveSteps, setLiveSteps] = useState<Array<Record<string, unknown>>>([]);
  const [policyDenial, setPolicyDenial] = useState<PolicyDenial | null>(null);
  const [busy, setBusy] = useState(false);
  const [saveBusy, setSaveBusy] = useState(false);
  const [streaming, setStreaming] = useState(true);

  const load = useCallback(() => {
    api
      .listResources(ns)
      .then((r) => {
        setResources(r.resources);
        const collabAgents = r.resources.filter(
          (x) =>
            x.kind === "Agent" &&
            Boolean((x.spec as Spec).collaboration || (x.spec as Spec).supervisorRef),
        );
        if (collabAgents[0]) {
          setAgentRef(`agents/${collabAgents[0].name}`);
        }
      })
      .catch((e) => onError(String(e.message ?? e)));
  }, [ns, onError]);

  useEffect(() => {
    load();
  }, [load]);

  const agents = resources.filter((r) => r.kind === "Agent");
  const selected = agents.find((a) => `agents/${a.name}` === agentRef);
  const collab = (selected?.spec as Spec | undefined)?.collaboration as Spec | undefined;

  useEffect(() => {
    const nextPattern = String(collab?.pattern ?? "planner_executor_reviewer");
    setPattern(nextPattern);
    setMaxIterations(Number(collab?.maxIterations ?? 2));
    const roles = collaborationRoles(nextPattern);
    const agentsMap = (collab?.agents as Record<string, string> | undefined) ?? {};
    const next: Record<string, string> = {};
    for (const role of roles) {
      next[role] =
        agentsMap[role] ??
        (role === "worker"
          ? Object.entries(agentsMap).find(([k]) => k.startsWith("worker"))?.[1] ??
            (agents[0] ? `agents/${agents[0].name}` : "")
          : agentsMap[role] ?? (agents[0] ? `agents/${agents[0].name}` : ""));
    }
    // Preserve extra worker* keys
    for (const [k, v] of Object.entries(agentsMap)) {
      if (k.startsWith("worker") && k !== "worker") next[k] = v;
    }
    setWiring(next);
  }, [agentRef, selected?.name]); // eslint-disable-line react-hooks/exhaustive-deps

  const roles = collaborationRoles(pattern);
  const steps = (
    Array.isArray(result?.steps)
      ? (result?.steps as Array<Record<string, unknown>>)
      : liveSteps
  );
  const errors = Array.isArray(result?.errors)
    ? (result?.errors as Array<Record<string, unknown>>)
    : [];
  const status = String(result?.status ?? (busy ? "running" : ""));

  async function run() {
    setBusy(true);
    setResult(null);
    setLiveSteps([]);
    setPolicyDenial(null);
    try {
      const payload = JSON.parse(input) as Record<string, unknown>;
      const collaboration = {
        pattern,
        maxIterations,
        sharedContext: true,
        agents: wiring,
      };
      if (streaming) {
        let finalData: Record<string, unknown> | null = null;
        const collected: Array<Record<string, unknown>> = [];
        for await (const event of api.runResourceStream(
          ns,
          agentRef,
          payload,
          true,
          collaboration,
        )) {
          if (event.type === "turn" && event.data?.step) {
            const step = event.data.step as Record<string, unknown>;
            collected.push(step);
            setLiveSteps([...collected]);
          } else if (event.type === "done" || event.type === "error") {
            finalData = (event.data ?? event) as Record<string, unknown>;
            setResult(finalData);
            const denial = denialFromExecution(finalData);
            if (denial) setPolicyDenial(denial);
          }
        }
        if (!finalData) {
          setResult({ status: "completed", steps: collected });
        }
      } else {
        const r = await api.runResource(ns, agentRef, payload, true, collaboration);
        const data = (r.data ?? r) as unknown as Record<string, unknown>;
        setResult(data);
        const denial = denialFromExecution(data);
        if (denial) setPolicyDenial(denial);
      }
    } catch (e) {
      const denial =
        e && typeof e === "object" && "denial" in e
          ? ((e as { denial: PolicyDenial | null }).denial ?? null)
          : null;
      if (denial) setPolicyDenial(denial);
      onError(formatError(e));
    } finally {
      setBusy(false);
    }
  }

  async function saveWiring() {
    if (!selected) return;
    setSaveBusy(true);
    try {
      const nextSpec = {
        ...(selected.spec as Spec),
        collaboration: {
          pattern,
          maxIterations,
          sharedContext: true,
          agents: wiring,
        },
      };
      await api.upsertResource(ns, "Agent", selected.name, selected.version, nextSpec);
      await api.publishResource(ns, "Agent", selected.name, selected.version);
      await load();
    } catch (e) {
      onError(String((e as Error).message ?? e));
    } finally {
      setSaveBusy(false);
    }
  }

  return (
    <section>
      <header className="page-header row">
        <div>
          <h1>Multi-agent collaboration</h1>
          <p className="muted">
            Wire roles to published agents, run a pattern, and inspect the turn timeline with
            failure diagnosis.
          </p>
        </div>
        <div className="form-row">
          <label className="check-row">
            <input
              type="checkbox"
              checked={streaming}
              onChange={(e) => setStreaming(e.target.checked)}
            />
            Live stream turns
          </label>
          <button className="ghost" disabled={saveBusy || !selected} onClick={() => void saveWiring()}>
            {saveBusy ? "Saving…" : "Save wiring"}
          </button>
          <button className="primary" disabled={busy || agents.length === 0} onClick={() => void run()}>
            {busy ? (streaming ? "Streaming…" : "Running…") : "Run collaboration"}
          </button>
        </div>
      </header>

      <div className="form-row">
        <select value={agentRef} onChange={(e) => setAgentRef(e.target.value)}>
          {agents.map((a) => (
            <option key={a.name} value={`agents/${a.name}`}>
              agents/{a.name}
              {(a.spec as Spec).collaboration ? " · collab" : ""}
            </option>
          ))}
        </select>
        <select value={pattern} onChange={(e) => setPattern(e.target.value)}>
          <option value="planner_executor_reviewer">planner → executor → reviewer</option>
          <option value="supervisor_workers">supervisor → workers</option>
          <option value="hierarchical">hierarchical</option>
          <option value="peer_round_robin">peer round-robin</option>
        </select>
        <label className="check-row">
          Max iterations
          <input
            type="number"
            min={1}
            max={8}
            value={maxIterations}
            onChange={(e) => setMaxIterations(Number(e.target.value) || 1)}
            style={{ width: "4rem" }}
          />
        </label>
      </div>

      <div className="split" style={{ marginBottom: "1rem" }}>
        <div className="panel">
          <div className="form-section-title">Role wiring</div>
          <p className="muted form-hint">Map each pattern role to a published agent.</p>
          <div className="form-stack">
            {roles.map((role) => (
              <label key={role} className="wiring-row">
                <span className="mono">{role}</span>
                <select
                  value={wiring[role] ?? ""}
                  onChange={(e) => setWiring((w) => ({ ...w, [role]: e.target.value }))}
                >
                  <option value="">Select agent…</option>
                  {agents.map((a) => (
                    <option key={a.name} value={`agents/${a.name}`}>
                      agents/{a.name}
                    </option>
                  ))}
                </select>
              </label>
            ))}
          </div>
          <div style={{ marginTop: "1rem" }}>
            <FlowLane title="Pattern graph" nodes={collaborationNodes(pattern, wiring)} />
          </div>
        </div>

        <div className="panel">
          <div className="form-section-title">Input</div>
          <textarea
            className="code editor test-input"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            spellCheck={false}
          />
          {result && (
            <div style={{ marginTop: "0.75rem" }}>
              <span
                className={
                  status === "completed" ? "badge ok" : status === "partial" ? "badge warn" : "badge danger"
                }
              >
                {status || "done"}
              </span>{" "}
              <span className="muted mono">
                {String(result.pattern ?? pattern)} · {String(result.iterations ?? "—")} iteration(s)
              </span>
            </div>
          )}
        </div>
      </div>

      {policyDenial && <PolicyDenialCard denial={policyDenial} />}

      {errors.length > 0 && (
        <div className="panel" style={{ marginBottom: "1rem", borderColor: "var(--danger, #b33)" }}>
          <div className="form-section-title">Failure diagnosis</div>
          {errors.map((err, i) => (
            <div key={i} className="diagnosis-card">
              <div>
                <span className="badge danger">{String(err.code ?? "error")}</span>{" "}
                <span className="mono">
                  {String(err.role ?? "")}
                  {err.ref ? ` · ${String(err.ref)}` : ""}
                </span>
              </div>
              <div>{String(err.message ?? "")}</div>
              {err.diagnosis != null && err.diagnosis !== "" ? (
                <p className="muted">{String(err.diagnosis)}</p>
              ) : null}
            </div>
          ))}
        </div>
      )}

      <div className="panel">
        <div className="form-section-title">
          Turn timeline {busy && streaming ? <span className="badge warn">live</span> : null}
        </div>
        {steps.length === 0 ? (
          <p className="muted">
            {busy
              ? "Waiting for first turn…"
              : "Run a collaboration to see planner/executor/reviewer turns."}
          </p>
        ) : (
          <ol className="turn-timeline">
            {steps.map((step, i) => {
              const st = String(step.status ?? "ok");
              return (
                <li key={i} className={`turn-item turn-${st}`}>
                  <div className="turn-head">
                    <span className="turn-index">#{Number(step.turn ?? i + 1)}</span>
                    <span className="mono">{String(step.role)}</span>
                    <span className="muted mono">{String(step.ref ?? "")}</span>
                    <span
                      className={
                        st === "ok" ? "badge ok" : st === "paused" ? "badge warn" : "badge danger"
                      }
                    >
                      {st}
                    </span>
                    {step.latencyMs != null && (
                      <span className="muted mono">{Number(step.latencyMs).toFixed(0)} ms</span>
                    )}
                  </div>
                  {step.preview != null && <div className="turn-preview">{String(step.preview)}</div>}
                  {step.error != null && <div className="turn-error">{String(step.error)}</div>}
                  {step.diagnosis != null && <div className="muted">{String(step.diagnosis)}</div>}
                </li>
              );
            })}
          </ol>
        )}
      </div>

      {result && (
        <details style={{ marginTop: "1rem" }}>
          <summary className="muted">Raw result JSON</summary>
          <pre className="code">{JSON.stringify(result, null, 2)}</pre>
        </details>
      )}
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
  const [publishedAgents, setPublishedAgents] = useState<Resource[]>([]);
  const [cap, setCap] = useState("research");
  const [registerRef, setRegisterRef] = useState("agents/support-agent");
  const [registerAddress, setRegisterAddress] = useState("support@platform.local");
  const [registerCaps, setRegisterCaps] = useState("support, refund");
  const [registerSchemas, setRegisterSchemas] = useState("");
  const [deliveryMode, setDeliveryMode] = useState("pull");
  const [registering, setRegistering] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  const load = useCallback(() => {
    Promise.all([api.listAgents(ns), api.listResources(ns)])
      .then(([discovery, catalog]) => {
        setAgents(discovery.agents);
        setPublishedAgents(catalog.resources.filter((resource) => resource.kind === "Agent"));
      })
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

      <div className="panel discovery-register">
        <div>
          <h2>Register capabilities</h2>
          <p className="muted">
            Add or update what a published agent can handle. This powers capability routing.
          </p>
        </div>
        <div className="resource-form">
          <label className="form-field">
            <span className="form-label">Agent</span>
            <select
              value={registerRef}
              onChange={(e) => {
                const ref = e.target.value;
                setRegisterRef(ref);
                setRegisterAddress(`${ref.replace("agents/", "")}@platform.local`);
              }}
            >
              {publishedAgents.map((agent) => (
                <option key={agent.name} value={`agents/${agent.name}`}>
                  agents/{agent.name}
                </option>
              ))}
              {registerRef &&
                !publishedAgents.some((agent) => `agents/${agent.name}` === registerRef) && (
                  <option value={registerRef}>{registerRef}</option>
                )}
            </select>
          </label>
          <label className="form-field">
            <span className="form-label">Address</span>
            <input
              value={registerAddress}
              onChange={(e) => setRegisterAddress(e.target.value)}
              placeholder="support@platform.local"
            />
          </label>
          <label className="form-field">
            <span className="form-label">Capabilities</span>
            <span className="form-hint muted">Comma-separated, e.g. support, refund</span>
            <input
              value={registerCaps}
              onChange={(e) => setRegisterCaps(e.target.value)}
              placeholder="support, refund"
            />
          </label>
          <label className="form-field">
            <span className="form-label">Input/output schemas (optional)</span>
            <span className="form-hint muted">Comma-separated schema refs</span>
            <input
              value={registerSchemas}
              onChange={(e) => setRegisterSchemas(e.target.value)}
              placeholder="schemas/support-request"
            />
          </label>
          <label className="form-field">
            <span className="form-label">Delivery mode</span>
            <select value={deliveryMode} onChange={(e) => setDeliveryMode(e.target.value)}>
              <option value="pull">pull</option>
              <option value="push">push</option>
            </select>
          </label>
          <button
            className="primary"
            disabled={registering || !registerRef || !registerCaps.trim()}
            onClick={async () => {
              setRegistering(true);
              setNotice(null);
              try {
                await api.registerCapability(ns, {
                  agent_ref: registerRef,
                  address: registerAddress || undefined,
                  capabilities: registerCaps
                    .split(",")
                    .map((item) => item.trim())
                    .filter(Boolean),
                  schemas: registerSchemas
                    .split(",")
                    .map((item) => item.trim())
                    .filter(Boolean),
                  delivery_mode: deliveryMode,
                });
                setNotice(`Registered capabilities for ${registerRef}`);
                load();
              } catch (e) {
                onError(String((e as Error).message ?? e));
              } finally {
                setRegistering(false);
              }
            }}
          >
            {registering ? "Registering…" : "Register / update"}
          </button>
          {notice && <span className="badge ok">{notice}</span>}
        </div>
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

function WorkflowsView({
  ns,
  onError,
  go,
}: {
  ns: string;
  onError: (e: string) => void;
  go: (v: View) => void;
}) {
  const [goal, setGoal] = useState("Research market data across competitors");
  const [plan, setPlan] = useState<{
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
    output: Record<string, unknown>;
  } | null>(null);
  const [busy, setBusy] = useState(false);
  const [published, setPublished] = useState<Resource[]>([]);
  const [plannerMode, setPlannerMode] = useState<"auto" | "llm" | "heuristic">("auto");

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
          Describe a goal — the <strong>LLM planner</strong> builds a step graph (heuristic
          fallback if needed), then runs it.
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
        <select
          value={plannerMode}
          onChange={(e) => setPlannerMode(e.target.value as "auto" | "llm" | "heuristic")}
        >
          <option value="auto">Planner: auto (LLM → heuristic)</option>
          <option value="llm">Planner: LLM only</option>
          <option value="heuristic">Planner: heuristic only</option>
        </select>
        <button
          className="primary"
          disabled={busy}
          onClick={async () => {
            setBusy(true);
            setPlan(null);
            try {
              setPlan(await api.planWorkflow(ns, goal, plannerMode));
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
            title={`Plan · ${plan.ir.name} · ${plan.status} · ${
              plan.ir.plannerBackend ?? plan.ir.planner_backend ?? "planner"
            }`}
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

      <HitlPanel ns={ns} onError={onError} go={go} />
    </section>
  );
}

function HitlPanel({
  ns,
  onError,
  go,
}: {
  ns: string;
  onError: (e: string) => void;
  go: (v: View) => void;
}) {
  const [runId, setRunId] = useState("");
  const [hitlResult, setHitlResult] = useState<string | null>(null);
  return (
    <div className="panel" style={{ marginTop: "1.5rem" }}>
      <div className="form-section-title">Human approval (HITL)</div>
      <p className="muted">
        Approve or resume a paused durable workflow run by id, or open the full inbox.
      </p>
      <div className="form-row">
        <input value={runId} onChange={(e) => setRunId(e.target.value)} placeholder="run id" />
        <button
          disabled={!runId}
          onClick={async () => {
            try {
              setHitlResult(JSON.stringify(await api.approveWorkflow(runId, "approved"), null, 2));
            } catch (e) {
              onError(String((e as Error).message ?? e));
            }
          }}
        >
          Approve
        </button>
        <button
          disabled={!runId}
          onClick={async () => {
            try {
              setHitlResult(JSON.stringify(await api.resumeWorkflow(runId, ns), null, 2));
            } catch (e) {
              onError(String((e as Error).message ?? e));
            }
          }}
        >
          Resume
        </button>
        <button className="ghost" onClick={() => go("hitl")}>
          Open inbox
        </button>
      </div>
      {hitlResult && <pre className="code">{hitlResult}</pre>}
    </div>
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
  const [name, setName] = useState("OPENAI_API_KEY");
  const [value, setValue] = useState("");
  const [visible, setVisible] = useState(false);
  const [storing, setStoring] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

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
      <div className="panel secret-create-panel">
        <div>
          <h2>Add or rotate a secret</h2>
          <p className="muted">
            Values are encrypted and never returned by the list API. Reusing a name rotates it.
          </p>
        </div>
        <div className="form-row secret-form-row">
          <label className="form-field">
            <span className="form-label">Secret name</span>
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="OPENAI_API_KEY"
            />
          </label>
          <label className="form-field">
            <span className="form-label">Secret value</span>
            <input
              type={visible ? "text" : "password"}
              value={value}
              onChange={(e) => setValue(e.target.value)}
              placeholder="Paste value"
              autoComplete="new-password"
            />
          </label>
          <button type="button" onClick={() => setVisible((current) => !current)}>
            {visible ? "Hide" : "Show"}
          </button>
          <button
            className="primary"
            disabled={storing || !name.trim() || !value}
            onClick={async () => {
              setStoring(true);
              setNotice(null);
              try {
                await api.putSecret(ns, name.trim(), value);
                setValue("");
                setVisible(false);
                setNotice(`Stored secrets/${name.trim()}`);
                load();
              } catch (e) {
                onError(String((e as Error).message ?? e));
              } finally {
                setStoring(false);
              }
            }}
          >
            {storing ? "Encrypting…" : "Store secret"}
          </button>
        </div>
        {notice && <span className="badge ok">{notice}</span>}
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

function ComplianceView({
  ns,
  user,
  onError,
}: {
  ns: string;
  user: AuthUser | null;
  onError: (e: string) => void;
}) {
  const [packs, setPacks] = useState<CompliancePack[]>([]);
  const [status, setStatus] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

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
        <p className="muted">Install HIPAA, PCI, GDPR, SOC2 baseline policies into the namespace.</p>
      </header>
      {status && <p className="muted">{status}</p>}
      <div className="pack-grid">
        {packs.map((p) => (
          <div key={p.id} className="pack">
            <div className="badge">{p.framework}</div>
            <h3>{p.name}</h3>
            <p className="muted">{p.description}</p>
            <span className="mono muted">
              {p.id} · v{p.version}
            </span>
            <button
              className="primary"
              style={{ marginTop: "0.75rem" }}
              disabled={busy === p.id}
              onClick={async () => {
                setBusy(p.id);
                setStatus(null);
                try {
                  const r = await api.installCompliance(ns, p.id, user?.email);
                  setStatus(`Installed ${p.id}: ${JSON.stringify(r)}`);
                } catch (e) {
                  onError(String((e as Error).message ?? e));
                } finally {
                  setBusy(null);
                }
              }}
            >
              {busy === p.id ? "Installing…" : "Install into namespace"}
            </button>
          </div>
        ))}
      </div>
    </section>
  );
}

function PromotionView({
  ns,
  user,
  onError,
}: {
  ns: string;
  user: AuthUser | null;
  onError: (e: string) => void;
}) {
  const [fromEnv, setFromEnv] = useState("development");
  const [toEnv, setToEnv] = useState("staging");
  const [promoId, setPromoId] = useState("");
  const [result, setResult] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const who = user?.email ?? "console";

  return (
    <section>
      <header className="page-header">
        <h1>Environment promotion</h1>
        <p className="muted">
          Move published bundles across environments. If the target Environment CRD requires
          approval, approve the promotion id below.
        </p>
      </header>
      <div className="form-row">
        <input value={fromEnv} onChange={(e) => setFromEnv(e.target.value)} placeholder="from env" />
        <input value={toEnv} onChange={(e) => setToEnv(e.target.value)} placeholder="to env" />
        <button
          className="primary"
          disabled={busy}
          onClick={async () => {
            setBusy(true);
            try {
              const r = await api.promote(ns, fromEnv, toEnv, who);
              setPromoId(r.promotionId);
              setResult(JSON.stringify(r, null, 2));
            } catch (e) {
              onError(String((e as Error).message ?? e));
            } finally {
              setBusy(false);
            }
          }}
        >
          Request promote
        </button>
      </div>
      <div className="form-row" style={{ marginTop: "0.75rem" }}>
        <input
          value={promoId}
          onChange={(e) => setPromoId(e.target.value)}
          placeholder="promotion id"
        />
        <button
          disabled={!promoId || busy}
          onClick={async () => {
            setBusy(true);
            try {
              const r = await api.approvePromotion(promoId, who);
              setResult(JSON.stringify(r, null, 2));
            } catch (e) {
              onError(String((e as Error).message ?? e));
            } finally {
              setBusy(false);
            }
          }}
        >
          Approve promotion
        </button>
      </div>
      {result && <pre className="code" style={{ marginTop: "1rem" }}>{result}</pre>}
    </section>
  );
}

function MarketplaceView({ ns, onError }: { ns: string; onError: (e: string) => void }) {
  const [plugins, setPlugins] = useState<MarketplacePlugin[]>([]);
  const [status, setStatus] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback(() => {
    api
      .listMarketplace()
      .then((r) => setPlugins(r.plugins))
      .catch((e) => onError(String(e.message ?? e)));
  }, [onError]);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <section>
      <header className="page-header row">
        <div>
          <h1>Marketplace</h1>
          <p className="muted">Install verified plugins into this namespace.</p>
        </div>
        <button onClick={load}>Refresh</button>
      </header>
      {status && <p className="muted">{status}</p>}
      <div className="pack-grid">
        {plugins.map((p) => (
          <div key={p.id ?? p.name} className="pack">
            {p.tier && <div className="badge">{p.tier}</div>}
            <h3>{p.name}</h3>
            <p className="muted">{p.description ?? "Plugin pack"}</p>
            <span className="mono muted">v{p.version ?? "—"}</span>
            <button
              className="primary"
              style={{ marginTop: "0.75rem" }}
              disabled={busy === p.name}
              onClick={async () => {
                setBusy(p.name);
                try {
                  const r = await api.installMarketplace(ns, p.name, p.version);
                  setStatus(`Installed ${p.name}: ${JSON.stringify(r)}`);
                } catch (e) {
                  onError(String((e as Error).message ?? e));
                } finally {
                  setBusy(null);
                }
              }}
            >
              {busy === p.name ? "Installing…" : "Install"}
            </button>
          </div>
        ))}
        {plugins.length === 0 && (
          <p className="muted">No plugins published yet. Use the API to publish a plugin manifest.</p>
        )}
      </div>
    </section>
  );
}

function pct(rate: number) {
  return `${(rate * 100).toFixed(1)}%`;
}

function EvaluationsView({ ns, onError }: { ns: string; onError: (e: string) => void }) {
  const [suiteRef, setSuiteRef] = useState("evaluationsuites/support-quality");
  const [targetRef, setTargetRef] = useState("agents/support-agent");
  const [result, setResult] = useState<Record<string, unknown> | null>(null);
  const [runs, setRuns] = useState<Record<string, unknown>[]>([]);
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    api
      .recentEvaluations(ns)
      .then((r) => setRuns(r.runs))
      .catch((e) => onError(String(e.message ?? e)));
  }, [ns, onError]);

  useEffect(() => {
    load();
  }, [load]);

  async function run() {
    setBusy(true);
    try {
      const r = await api.runEvaluation(ns, { suiteRef, targetRef });
      setResult(r as unknown as Record<string, unknown>);
      load();
    } catch (e) {
      onError(String((e as Error).message ?? e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section>
      <header className="page-header">
        <h1>Evaluations</h1>
        <p className="muted">
          Dry-run publish quality gates with keyword, latency, tool accuracy, and LLM judges.
        </p>
      </header>
      <div className="form-row">
        <input
          value={suiteRef}
          onChange={(e) => setSuiteRef(e.target.value)}
          placeholder="evaluationsuites/support-quality"
        />
        <input
          value={targetRef}
          onChange={(e) => setTargetRef(e.target.value)}
          placeholder="agents/support-agent"
        />
        <button className="primary" disabled={busy} onClick={run}>
          {busy ? "Running…" : "Run suite"}
        </button>
      </div>
      {result && (
        <div className="panel" style={{ marginTop: "1rem" }}>
          <div className="form-section-title">
            Latest run{" "}
            <span className={`badge ${result.passed ? "ok" : ""}`}>
              {result.passed ? "passed" : "failed"}
            </span>
          </div>
          <pre className="code small">{JSON.stringify(result, null, 2)}</pre>
        </div>
      )}
      <div className="panel" style={{ marginTop: "1rem" }}>
        <div className="form-section-title">Recent runs</div>
        {runs.length === 0 ? (
          <p className="muted">No evaluation runs yet.</p>
        ) : (
          <ul className="plain-list">
            {runs.map((r) => (
              <li key={String(r.runId)}>
                <span className="mono">{String(r.targetRef)}</span>{" "}
                <span className={`badge ${r.passed ? "ok" : ""}`}>
                  {r.passed ? "pass" : "fail"}
                </span>{" "}
                overall {Number(r.overall ?? 0).toFixed(2)}
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}

function GitSyncView({
  ns,
  user,
  onError,
}: {
  ns: string;
  user: AuthUser | null;
  onError: (e: string) => void;
}) {
  const [directory, setDirectory] = useState("examples/resources");
  const [exportDir, setExportDir] = useState("./export");
  const [publish, setPublish] = useState(true);
  const [result, setResult] = useState<string | null>(null);
  const [repos, setRepos] = useState<
    Array<{
      id: string;
      repoPath: string;
      branch: string;
      lastSyncAt: string | null;
      lastCommit: string | null;
      status: string;
    }>
  >([]);
  const [busy, setBusy] = useState(false);

  const loadRepos = useCallback(() => {
    api
      .listGitRepos(ns)
      .then((r) => setRepos(r.repos))
      .catch((e) => onError(String(e.message ?? e)));
  }, [ns, onError]);

  useEffect(() => {
    loadRepos();
  }, [loadRepos]);

  async function syncFromDir() {
    setBusy(true);
    setResult(null);
    try {
      const r = await api.gitSync(ns, {
        directory,
        publish,
        author: user?.email ?? "console",
      });
      setResult(JSON.stringify(r, null, 2));
      loadRepos();
    } catch (e) {
      onError(String((e as Error).message ?? e));
    } finally {
      setBusy(false);
    }
  }

  async function exportYaml() {
    setBusy(true);
    setResult(null);
    try {
      const r = await api.gitExport(ns, exportDir);
      setResult(JSON.stringify(r, null, 2));
    } catch (e) {
      onError(String((e as Error).message ?? e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section>
      <header className="page-header">
        <h1>Git sync</h1>
        <p className="muted">
          Apply CRD YAML from a local directory into the registry, or export published resources
          back to YAML for git.
        </p>
      </header>

      <div className="panel">
        <div className="form-section-title">Sync from directory</div>
        <p className="muted form-hint">
          Path is resolved on the API host (e.g. <span className="mono">examples/resources</span>).
        </p>
        <div className="form-row">
          <input
            value={directory}
            onChange={(e) => setDirectory(e.target.value)}
            placeholder="examples/resources"
            style={{ minWidth: "18rem" }}
          />
          <label className="check-row">
            <input
              type="checkbox"
              checked={publish}
              onChange={(e) => setPublish(e.target.checked)}
            />
            Publish after apply
          </label>
          <button className="primary" disabled={busy} onClick={syncFromDir}>
            {busy ? "Working…" : "Sync now"}
          </button>
        </div>
      </div>

      <div className="panel" style={{ marginTop: "1rem" }}>
        <div className="form-section-title">Export published → YAML</div>
        <div className="form-row">
          <input
            value={exportDir}
            onChange={(e) => setExportDir(e.target.value)}
            placeholder="./export"
          />
          <button disabled={busy} onClick={exportYaml}>
            Export YAML
          </button>
        </div>
      </div>

      <div className="panel" style={{ marginTop: "1rem" }}>
        <div className="form-section-title">Registered sync paths</div>
        {repos.length === 0 ? (
          <p className="muted">No syncs yet.</p>
        ) : (
          <div className="list">
            {repos.map((r) => (
              <div key={r.id} className="list-item static">
                <div>
                  <span className="mono">{r.repoPath}</span>
                  <div className="muted">
                    {r.branch} · {r.status}
                    {r.lastCommit ? ` · ${r.lastCommit}` : ""}
                    {r.lastSyncAt ? ` · ${r.lastSyncAt}` : ""}
                  </div>
                </div>
                <button
                  onClick={() => {
                    setDirectory(r.repoPath);
                  }}
                >
                  Use path
                </button>
              </div>
            ))}
          </div>
        )}
      </div>

      {result && (
        <pre className="code" style={{ marginTop: "1rem" }}>
          {result}
        </pre>
      )}
    </section>
  );
}

function TerraformView({ ns, onError }: { ns: string; onError: (e: string) => void }) {
  const [directory, setDirectory] = useState("./terraform");
  const [files, setFiles] = useState<Record<string, string>>({});
  const [selected, setSelected] = useState<string>("provider.tf");
  const [resourceCount, setResourceCount] = useState(0);
  const [status, setStatus] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const loadPreview = useCallback(async () => {
    try {
      const r = await api.terraformPreview(ns);
      setFiles(r.files);
      setResourceCount(r.resourceCount);
      const names = Object.keys(r.files);
      setSelected((prev) => (names.includes(prev) ? prev : names[0] ?? "provider.tf"));
    } catch (e) {
      onError(String((e as Error).message ?? e));
    }
  }, [ns, onError]);

  useEffect(() => {
    void loadPreview();
  }, [loadPreview]);

  async function exportToDisk() {
    setBusy(true);
    setStatus(null);
    try {
      const r = await api.terraformExport(ns, directory, true);
      setStatus(`Wrote ${r.exported} resource file(s) to ${r.directory}`);
      if (r.preview) {
        setFiles((prev) => ({ ...prev, ...r.preview }));
      }
      await loadPreview();
    } catch (e) {
      onError(String((e as Error).message ?? e));
    } finally {
      setBusy(false);
    }
  }

  const fileNames = Object.keys(files).sort((a, b) => {
    const rank = (n: string) =>
      n === "provider.tf" ? 0 : n === "variables.tf" ? 1 : n === "exported.json" ? 99 : 2;
    return rank(a) - rank(b) || a.localeCompare(b);
  });

  return (
    <section>
      <header className="page-header row">
        <div>
          <h1>Terraform</h1>
          <p className="muted">
            Preview and export published resources as HCL / terraform-json for IaC pipelines.
          </p>
        </div>
        <button disabled={busy} onClick={() => void loadPreview()}>
          Refresh preview
        </button>
      </header>

      <div className="form-row">
        <span className="badge">{resourceCount} resources</span>
        <input
          value={directory}
          onChange={(e) => setDirectory(e.target.value)}
          placeholder="./terraform"
        />
        <button className="primary" disabled={busy} onClick={exportToDisk}>
          {busy ? "Writing…" : "Write to disk"}
        </button>
        {status && <span className="badge ok">{status}</span>}
      </div>

      {fileNames.length === 0 ? (
        <p className="muted" style={{ marginTop: "1rem" }}>
          No published resources to export. Publish agents/prompts first, or sync from{" "}
          <span className="mono">examples/resources</span>.
        </p>
      ) : (
        <div className="editor-split" style={{ marginTop: "1rem" }}>
          <div className="panel">
            <div className="form-section-title">Files</div>
            <div className="list">
              {fileNames.map((name) => (
                <button
                  key={name}
                  className={`list-item ${selected === name ? "active" : ""}`}
                  onClick={() => setSelected(name)}
                  style={{ width: "100%", textAlign: "left" }}
                >
                  <span className="mono">{name}</span>
                </button>
              ))}
            </div>
          </div>
          <div className="panel">
            <div className="form-section-title">{selected}</div>
            <pre className="code small preview-json">{files[selected] ?? ""}</pre>
          </div>
        </div>
      )}
    </section>
  );
}

function HitlInboxView({ ns, onError }: { ns: string; onError: (e: string) => void }) {
  const [items, setItems] = useState<HitlInboxItem[]>([]);
  const [selected, setSelected] = useState<HitlInboxItem | null>(null);
  const [detail, setDetail] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [scopeNs, setScopeNs] = useState(true);

  const load = useCallback(async () => {
    try {
      const r = await api.listHitlInbox(scopeNs ? ns : undefined, 50);
      setItems(r.items);
      setSelected((prev) => {
        if (!prev) return null;
        return r.items.find((i) => i.runId === prev.runId) ?? null;
      });
    } catch (e) {
      onError(String((e as Error).message ?? e));
    }
  }, [ns, onError, scopeNs]);

  useEffect(() => {
    void load();
  }, [load]);

  async function openRun(item: HitlInboxItem) {
    setSelected(item);
    try {
      const full = await api.getWorkflowRun(item.runId);
      setDetail(JSON.stringify(full, null, 2));
    } catch (e) {
      onError(String((e as Error).message ?? e));
    }
  }

  async function act(decision: "approved" | "rejected") {
    if (!selected) return;
    setBusy(true);
    try {
      const approved = await api.approveWorkflow(selected.runId, decision);
      let result: Record<string, unknown> = approved;
      if (decision === "approved") {
        result = await api.resumeWorkflow(selected.runId, ns);
      }
      setDetail(JSON.stringify(result, null, 2));
      await load();
    } catch (e) {
      onError(String((e as Error).message ?? e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section>
      <header className="page-header row">
        <div>
          <h1>HITL inbox</h1>
          <p className="muted">
            Workflow runs waiting on human approval or rate-limit override. Approve + resume, or
            reject.
          </p>
        </div>
        <div className="form-row">
          <label className="check-row">
            <input
              type="checkbox"
              checked={scopeNs}
              onChange={(e) => setScopeNs(e.target.checked)}
            />
            This namespace only
          </label>
          <button onClick={() => void load()}>Refresh</button>
        </div>
      </header>

      <div className="metric-grid" style={{ marginBottom: "1rem" }}>
        <div className="metric-card">
          <div className="metric-label">Waiting</div>
          <div className="metric-value">{items.length}</div>
          <div className="muted mono">{scopeNs ? ns : "all namespaces"}</div>
        </div>
      </div>

      <div className="split">
        <div className="panel list">
          <div className="form-section-title">Pending runs</div>
          {items.length === 0 ? (
            <p className="muted" style={{ padding: "0.75rem" }}>
              No runs waiting for approval.
            </p>
          ) : (
            items.map((item) => {
              const pending = item.pendingApproval;
              const step =
                pending?.step_id ?? item.currentStepId ?? "—";
              const reason = pending?.reason ?? "approval_required";
              return (
                <button
                  key={item.runId}
                  className={
                    selected?.runId === item.runId ? "list-item active" : "list-item"
                  }
                  data-testid={`hitl-run-${item.runId}`}
                  onClick={() => void openRun(item)}
                >
                  <div>
                    <div className="mono">{item.workflowRef ?? item.runId}</div>
                    <div className="muted">
                      step <span className="mono">{step}</span> · {reason}
                    </div>
                  </div>
                  <span className="badge warn">{item.status}</span>
                </button>
              );
            })
          )}
        </div>
        <div className="panel">
          <div className="form-section-title">Decision</div>
          {!selected ? (
            <p className="muted">Select a waiting run.</p>
          ) : (
            <>
              <p className="mono">{selected.runId}</p>
              <p className="muted">
                {selected.workflowRef ?? "workflow"} · step{" "}
                {selected.pendingApproval?.step_id ?? selected.currentStepId ?? "—"}
              </p>
              <div className="form-row" style={{ marginTop: "0.75rem" }}>
                <button
                  className="primary"
                  disabled={busy}
                  data-testid="hitl-approve"
                  onClick={() => void act("approved")}
                >
                  {busy ? "Working…" : "Approve & resume"}
                </button>
                <button
                  disabled={busy}
                  data-testid="hitl-reject"
                  onClick={() => void act("rejected")}
                >
                  Reject
                </button>
              </div>
              {detail && <pre className="code" style={{ marginTop: "1rem" }}>{detail}</pre>}
            </>
          )}
        </div>
      </div>
    </section>
  );
}

function RegionsView({ ns, onError }: { ns: string; onError: (e: string) => void }) {
  const [regions, setRegions] = useState<RegionInfo[]>([]);
  const [nodes, setNodes] = useState<EdgeNode[]>([]);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [endpoint, setEndpoint] = useState("http://localhost:8082");
  const [residency, setResidency] = useState("");
  const [isPrimary, setIsPrimary] = useState(false);
  const [edgeRegion, setEdgeRegion] = useState("");
  const [cachePath, setCachePath] = useState(".platform/bundle.cache.json");
  const [telemetry, setTelemetry] = useState<{
    eventCount?: number;
    onlineCount?: number;
    nodeCount?: number;
    series?: Array<{
      index: number;
      count: number;
      successRate: number | null;
      avgLatencyMs: number | null;
    }>;
  } | null>(null);

  const load = useCallback(async () => {
    try {
      const [r, e, t] = await Promise.all([
        api.listRegions(),
        api.listEdgeNodes(),
        api.listEdgeTelemetry({ hours: 24, summary: true }),
      ]);
      setRegions(r.regions);
      setNodes(e.nodes);
      setTelemetry(t);
      setEdgeRegion((prev) => prev || r.regions[0]?.name || "");
    } catch (err) {
      onError(formatError(err));
    }
  }, [onError]);

  useEffect(() => {
    void load();
  }, [load]);

  async function registerRegion() {
    if (!name.trim() || !endpoint.trim()) return;
    setBusy(true);
    try {
      const r = await api.registerRegion({
        name: name.trim(),
        endpoint: endpoint.trim(),
        dataResidency: residency.trim() || undefined,
        isPrimary,
      });
      setResult(JSON.stringify(r, null, 2));
      setName("");
      await load();
    } catch (e) {
      onError(formatError(e));
    } finally {
      setBusy(false);
    }
  }

  async function registerEdge() {
    setBusy(true);
    try {
      const r = await api.registerEdgeNode({
        namespace: ns,
        environment: "development",
        region: edgeRegion || undefined,
        bundleCachePath: cachePath || undefined,
      });
      setResult(JSON.stringify(r, null, 2));
      await load();
    } catch (e) {
      onError(formatError(e));
    } finally {
      setBusy(false);
    }
  }

  async function pingTelemetry(nodeId: string) {
    setBusy(true);
    try {
      const r = await api.postEdgeTelemetry(nodeId, [
        {
          type: "heartbeat",
          latencyMs: 20 + Math.round(Math.random() * 80),
          success: true,
        },
        {
          type: "sync",
          latencyMs: 40 + Math.round(Math.random() * 120),
          success: true,
        },
      ]);
      setResult(JSON.stringify(r, null, 2));
      await load();
    } catch (e) {
      onError(formatError(e));
    } finally {
      setBusy(false);
    }
  }

  const primary = regions.find((r) => r.is_primary);
  const online = nodes.filter((n) => n.status === "online").length;
  const series = telemetry?.series ?? [];
  const maxLatency = Math.max(
    1,
    ...series.map((s) => Number(s.avgLatencyMs ?? 0)),
  );
  const maxCount = Math.max(1, ...series.map((s) => s.count));

  return (
    <section>
      <header className="page-header row">
        <div>
          <h1>Regions & edge</h1>
          <p className="muted">
            Multi-region control-plane endpoints, failover, and edge runtime telemetry.
          </p>
        </div>
        <button onClick={() => void load()}>Refresh</button>
      </header>

      <div className="metric-grid">
        <div className="metric-card">
          <div className="metric-label">Regions</div>
          <div className="metric-value">{regions.length}</div>
          <div className="muted mono">primary · {primary?.name ?? "—"}</div>
        </div>
        <div className="metric-card">
          <div className="metric-label">Edge nodes</div>
          <div className="metric-value">{nodes.length}</div>
          <div className="muted mono">{online} online</div>
        </div>
        <div className="metric-card">
          <div className="metric-label">Telemetry (24h)</div>
          <div className="metric-value">{telemetry?.eventCount ?? 0}</div>
          <div className="muted mono">
            {telemetry?.onlineCount ?? online}/{telemetry?.nodeCount ?? nodes.length} online
          </div>
        </div>
      </div>

      <div className="panel" style={{ marginTop: "1.25rem" }}>
        <div className="form-section-title">Edge telemetry charts</div>
        {series.every((s) => s.count === 0) ? (
          <p className="muted">
            No telemetry yet. Register an edge node and click <em>Ping telemetry</em>.
          </p>
        ) : (
          <div className="split">
            <div>
              <div className="muted form-hint">Avg latency (ms)</div>
              {series.map((s) => (
                <div key={`lat-${s.index}`} className="metric-bar-row">
                  <div className="metric-bar-meta">
                    <span className="mono">t{s.index + 1}</span>
                    <span className="muted mono">
                      {s.avgLatencyMs != null ? `${s.avgLatencyMs} ms` : "—"}
                    </span>
                  </div>
                  <div className="metric-bar-track">
                    <div
                      className="metric-bar-fill"
                      style={{
                        width: `${Math.round(((s.avgLatencyMs ?? 0) / maxLatency) * 100)}%`,
                      }}
                    />
                  </div>
                </div>
              ))}
            </div>
            <div>
              <div className="muted form-hint">Event volume / success</div>
              {series.map((s) => (
                <div key={`vol-${s.index}`} className="metric-bar-row">
                  <div className="metric-bar-meta">
                    <span className="mono">n={s.count}</span>
                    <span className="muted mono">
                      {s.successRate != null
                        ? `${Math.round(s.successRate * 100)}% ok`
                        : "—"}
                    </span>
                  </div>
                  <div className="metric-bar-track">
                    <div
                      className="metric-bar-fill"
                      style={{
                        width: `${Math.round((s.count / maxCount) * 100)}%`,
                        opacity: s.successRate != null ? 0.45 + s.successRate * 0.55 : 0.6,
                      }}
                    />
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>

      <div className="split" style={{ marginTop: "1.25rem" }}>
        <div className="panel">
          <div className="form-section-title">Regions</div>
          {regions.length === 0 ? (
            <p className="muted">No regions registered.</p>
          ) : (
            <div className="list">
              {regions.map((r) => (
                <div key={r.id || r.name} className="list-item static">
                  <div>
                    <div>
                      <span className="mono">{r.name}</span>{" "}
                      {r.is_primary && <span className="badge ok">primary</span>}{" "}
                      <span
                        className={
                          r.status === "active"
                            ? "badge ok"
                            : r.status === "offline"
                              ? "badge danger"
                              : "badge warn"
                        }
                      >
                        {r.status}
                      </span>
                    </div>
                    <div className="muted mono">{r.endpoint}</div>
                    {r.data_residency && (
                      <div className="muted">residency · {r.data_residency}</div>
                    )}
                  </div>
                  <div className="form-row">
                    {!r.is_primary && r.status === "active" && (
                      <button
                        className="ghost"
                        disabled={busy}
                        onClick={async () => {
                          setBusy(true);
                          try {
                            setResult(
                              JSON.stringify(await api.setPrimaryRegion(r.name), null, 2),
                            );
                            await load();
                          } catch (e) {
                            onError(String((e as Error).message ?? e));
                          } finally {
                            setBusy(false);
                          }
                        }}
                      >
                        Make primary
                      </button>
                    )}
                    {r.is_primary && (
                      <button
                        disabled={busy}
                        onClick={async () => {
                          setBusy(true);
                          try {
                            setResult(
                              JSON.stringify(await api.failoverRegion(r.name), null, 2),
                            );
                            await load();
                          } catch (e) {
                            onError(String((e as Error).message ?? e));
                          } finally {
                            setBusy(false);
                          }
                        }}
                      >
                        Failover
                      </button>
                    )}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        <div className="panel">
          <div className="form-section-title">Register region</div>
          <div className="form-stack">
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="name (e.g. ap-south-1)"
            />
            <input
              value={endpoint}
              onChange={(e) => setEndpoint(e.target.value)}
              placeholder="endpoint URL"
            />
            <input
              value={residency}
              onChange={(e) => setResidency(e.target.value)}
              placeholder="data residency (optional)"
            />
            <label className="check-row">
              <input
                type="checkbox"
                checked={isPrimary}
                onChange={(e) => setIsPrimary(e.target.checked)}
              />
              Set as primary
            </label>
            <button className="primary" disabled={busy || !name.trim()} onClick={() => void registerRegion()}>
              Register
            </button>
          </div>
        </div>
      </div>

      <div className="split" style={{ marginTop: "1.25rem" }}>
        <div className="panel">
          <div className="form-section-title">Edge nodes</div>
          {nodes.length === 0 ? (
            <p className="muted">No edge runtimes registered yet.</p>
          ) : (
            <div className="list">
              {nodes.map((n) => (
                <div key={n.id} className="list-item static">
                  <div>
                    <div className="mono">{n.id}</div>
                    <div className="muted">
                      ns <span className="mono">{n.namespaceId}</span>
                      {n.regionName ? ` · ${n.regionName}` : ""}
                    </div>
                    <div className="muted mono">
                      sync {n.lastSyncAt ?? "—"} · telemetry {n.lastTelemetryAt ?? "—"}
                    </div>
                  </div>
                  <div className="form-row">
                    <button
                      className="ghost"
                      disabled={busy}
                      onClick={() => void pingTelemetry(n.id)}
                    >
                      Ping telemetry
                    </button>
                    <span className={n.status === "online" ? "badge ok" : "badge warn"}>
                      {n.status}
                    </span>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        <div className="panel">
          <div className="form-section-title">Register edge node</div>
          <p className="muted form-hint">
            Registers against current namespace <span className="mono">{ns}</span>.
          </p>
          <div className="form-stack">
            <select value={edgeRegion} onChange={(e) => setEdgeRegion(e.target.value)}>
              <option value="">No region preference</option>
              {regions.map((r) => (
                <option key={r.name} value={r.name}>
                  {r.name}
                </option>
              ))}
            </select>
            <input
              value={cachePath}
              onChange={(e) => setCachePath(e.target.value)}
              placeholder="bundle cache path"
            />
            <button className="primary" disabled={busy} onClick={() => void registerEdge()}>
              Register edge
            </button>
          </div>
        </div>
      </div>

      {result && <pre className="code" style={{ marginTop: "1rem" }}>{result}</pre>}
    </section>
  );
}

function IdentityView({ ns, onError }: { ns: string; onError: (e: string) => void }) {
  const orgId = ns.split("/")[0] || "default-org";
  const [users, setUsers] = useState<ScimUser[]>([]);
  const [email, setEmail] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const r = await api.scimListUsers(orgId);
      setUsers(r.Resources || []);
    } catch (e) {
      onError(String((e as Error).message ?? e));
    }
  }, [orgId, onError]);

  useEffect(() => {
    void load();
  }, [load]);

  async function createUser() {
    if (!email.trim()) return;
    setBusy(true);
    setNotice(null);
    try {
      await api.scimCreateUser(orgId, {
        userName: email.trim(),
        name: { formatted: displayName.trim() || email.trim() },
        emails: [{ value: email.trim(), primary: true }],
        active: true,
      });
      setEmail("");
      setDisplayName("");
      setNotice(`Created ${email.trim()}`);
      await load();
    } catch (e) {
      onError(String((e as Error).message ?? e));
    } finally {
      setBusy(false);
    }
  }

  async function deactivate(user: ScimUser) {
    setBusy(true);
    setNotice(null);
    try {
      await api.scimDeactivateUser(user.id);
      setNotice(`Deactivated ${user.userName}`);
      await load();
    } catch (e) {
      onError(String((e as Error).message ?? e));
    } finally {
      setBusy(false);
    }
  }

  const activeCount = users.filter((u) => u.active !== false).length;

  return (
    <section data-testid="identity-view">
      <header className="page-header row">
        <div>
          <h1>Identity (SCIM)</h1>
          <p className="muted">
            Org users via <span className="mono">/scim/v2/Users</span> for{" "}
            <span className="mono">{orgId}</span>. Deactivate soft-disables the account.
          </p>
        </div>
        <button onClick={() => void load()}>Refresh</button>
      </header>

      <div className="metric-grid" style={{ marginBottom: "1rem" }}>
        <div className="metric-card">
          <div className="metric-label">Users</div>
          <div className="metric-value" data-testid="identity-user-count">
            {users.length}
          </div>
          <div className="muted">{activeCount} active</div>
        </div>
      </div>

      <div className="panel secret-create-panel" style={{ marginBottom: "1rem" }}>
        <div>
          <h2>Create user</h2>
          <p className="muted">SCIM User create — email is the userName.</p>
        </div>
        <div className="form-row secret-form-row">
          <label className="form-field">
            <span className="form-label">Email</span>
            <input
              data-testid="identity-email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="user@example.com"
            />
          </label>
          <label className="form-field">
            <span className="form-label">Display name</span>
            <input
              data-testid="identity-display-name"
              value={displayName}
              onChange={(e) => setDisplayName(e.target.value)}
              placeholder="Optional"
            />
          </label>
          <button
            className="primary"
            disabled={busy || !email.trim()}
            data-testid="identity-create"
            onClick={() => void createUser()}
          >
            {busy ? "Saving…" : "Create"}
          </button>
        </div>
        {notice && <p className="muted">{notice}</p>}
      </div>

      <div className="panel list">
        <div className="form-section-title">Directory</div>
        {users.length === 0 ? (
          <p className="muted" style={{ padding: "0.75rem" }}>
            No users yet. Create one above or sign in once.
          </p>
        ) : (
          users.map((user) => (
            <div
              key={user.id}
              className="list-item"
              data-testid={`identity-user-${user.id}`}
              style={{ cursor: "default" }}
            >
              <div>
                <div className="mono">{user.userName}</div>
                <div className="muted">
                  {user.name?.formatted || "—"} ·{" "}
                  <span className="mono">{user.id}</span>
                </div>
              </div>
              <div className="form-row">
                <span className={user.active === false ? "badge warn" : "badge ok"}>
                  {user.active === false ? "inactive" : "active"}
                </span>
                {user.active !== false && (
                  <button
                    disabled={busy}
                    data-testid={`identity-deactivate-${user.id}`}
                    onClick={() => void deactivate(user)}
                  >
                    Deactivate
                  </button>
                )}
              </div>
            </div>
          ))
        )}
      </div>
    </section>
  );
}

function ActivityView({ ns, onError }: { ns: string; onError: (e: string) => void }) {
  const [events, setEvents] = useState<AuditEvent[]>([]);
  const [orgId, setOrgId] = useState("");
  const [selected, setSelected] = useState<AuditEvent | null>(null);
  const [filter, setFilter] = useState("");
  const [retentionDays, setRetentionDays] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [resultNotice, setResultNotice] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const r = await api.listAudit(ns, 80, filter || undefined);
      setEvents(r.events);
      setOrgId(r.orgId);
      if (r.retentionDays != null) setRetentionDays(r.retentionDays);
      setSelected((prev) => {
        if (!prev) return null;
        return r.events.find((e) => e.id === prev.id) ?? null;
      });
    } catch (e) {
      onError(formatError(e));
    }
  }, [ns, onError, filter]);

  useEffect(() => {
    void load();
  }, [load]);

  async function purge() {
    setBusy(true);
    try {
      const r = await api.purgeAudit(ns);
      setResultNotice(`Purged ${r.deleted} events older than ${r.retainDays}d`);
      await load();
    } catch (e) {
      onError(formatError(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section>
      <header className="page-header row">
        <div>
          <h1>Activity</h1>
          <p className="muted">
            Org audit trail for <span className="mono">{orgId || ns.split("/")[0]}</span>
            {retentionDays != null ? ` · retention ${retentionDays}d` : ""}.
          </p>
        </div>
        <div className="form-row">
          <select value={filter} onChange={(e) => setFilter(e.target.value)}>
            <option value="">All actions</option>
            <option value="resource.published">resource.published</option>
            <option value="resource.unpublished">resource.unpublished</option>
            <option value="resource.execute">resource.execute</option>
            <option value="policy.denied">policy.denied</option>
            <option value="mcp.call">mcp.call</option>
            <option value="auth.login">auth.login</option>
            <option value="secret.put">secret.put</option>
            <option value="environment.promoted">environment.promoted</option>
            <option value="region.registered">region.registered</option>
            <option value="region.failover">region.failover</option>
            <option value="edge.registered">edge.registered</option>
            <option value="audit.purged">audit.purged</option>
          </select>
          <button onClick={() => void load()}>Refresh</button>
          <button className="ghost" disabled={busy} onClick={() => void purge()}>
            {busy ? "Purging…" : "Purge old"}
          </button>
        </div>
      </header>

      {resultNotice && (
        <div className="banner" onClick={() => setResultNotice(null)}>
          {resultNotice}
        </div>
      )}

      <div className="metric-grid" style={{ marginBottom: "1rem" }}>
        <div className="metric-card">
          <div className="metric-label">Events</div>
          <div className="metric-value">{events.length}</div>
          <div className="muted mono">{filter || "all"}</div>
        </div>
      </div>

      <div className="split">
        <div className="panel list">
          <div className="form-section-title">Recent</div>
          {events.length === 0 ? (
            <p className="muted" style={{ padding: "0.75rem" }}>
              No audit events yet. Publish a resource or sign in to create one.
            </p>
          ) : (
            events.map((ev) => {
              const when = ev.createdAt ?? ev.created_at ?? "";
              const actor = ev.actorId ?? ev.actor_id ?? "—";
              const ref = ev.resourceRef ?? ev.resource_ref ?? "—";
              return (
                <button
                  key={ev.id}
                  className={selected?.id === ev.id ? "list-item active" : "list-item"}
                  onClick={() => setSelected(ev)}
                >
                  <div className="row" style={{ justifyContent: "space-between", gap: "0.5rem" }}>
                    <strong className="mono">{ev.action}</strong>
                    <span className="muted mono" style={{ fontSize: "0.75rem" }}>
                      {when ? new Date(when).toLocaleString() : ""}
                    </span>
                  </div>
                  <div className="muted" style={{ fontSize: "0.85rem" }}>
                    {String(actor)} · {String(ref)}
                  </div>
                </button>
              );
            })
          )}
        </div>
        <div className="panel">
          <div className="form-section-title">Detail</div>
          {selected ? (
            <pre className="code-block">{JSON.stringify(selected, null, 2)}</pre>
          ) : (
            <p className="muted">Select an event.</p>
          )}
        </div>
      </div>
    </section>
  );
}

function MetricsView({ ns, onError }: { ns: string; onError: (e: string) => void }) {
  const [overview, setOverview] = useState<MetricStats | null>(null);
  const [routes, setRoutes] = useState<Array<MetricStats & { routeName: string }>>([]);
  const [candidates, setCandidates] = useState<
    Array<MetricStats & { provider: string; model: string; key: string }>
  >([]);
  const [samples, setSamples] = useState<
    Array<{
      routeName: string;
      provider: string;
      model: string;
      latencyMs: number;
      success: boolean;
      costUnits: number;
      recordedAt: string;
    }>
  >([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [detail, setDetail] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [sampleCount, setSampleCount] = useState(0);

  const load = useCallback(async () => {
    try {
      const [summary, recent] = await Promise.all([
        api.metricsSummary(ns, 500),
        api.metricsRecent(ns, 40),
      ]);
      setOverview(summary.overview);
      setRoutes(summary.routes);
      setCandidates(summary.candidates);
      setSampleCount(summary.sampleCount);
      setSamples(recent.samples);
    } catch (e) {
      onError(String((e as Error).message ?? e));
    }
  }, [ns, onError]);

  useEffect(() => {
    void load();
  }, [load]);

  async function openRoute(routeName: string) {
    setSelected(routeName);
    try {
      const short = routeName.includes("/") ? routeName.split("/", 2)[1] : routeName;
      const r = await api.metricsRoute(ns, short);
      setDetail(JSON.stringify(r, null, 2));
    } catch (e) {
      onError(String((e as Error).message ?? e));
    }
  }

  async function tuneSelected() {
    if (!selected) return;
    setBusy(true);
    try {
      const short = selected.includes("/") ? selected.split("/", 2)[1] : selected;
      const r = await api.tuneModelRoute(ns, short, true);
      setDetail(JSON.stringify(r, null, 2));
      await load();
    } catch (e) {
      onError(String((e as Error).message ?? e));
    } finally {
      setBusy(false);
    }
  }

  const maxReq = Math.max(1, ...candidates.map((c) => c.requests));

  return (
    <section>
      <header className="page-header row">
        <div>
          <h1>Metrics</h1>
          <p className="muted">
            Model-route latency, success rate, and cost. Agent runs record samples automatically.
            Prometheus scrape: <span className="mono">GET /metrics</span>
          </p>
        </div>
        <button onClick={() => void load()}>Refresh</button>
      </header>

      {overview && (
        <div className="metric-grid">
          <div className="metric-card">
            <div className="metric-label">Requests</div>
            <div className="metric-value">{overview.requests}</div>
            <div className="muted mono">window · {sampleCount} samples</div>
          </div>
          <div className="metric-card">
            <div className="metric-label">Success rate</div>
            <div className="metric-value">{pct(overview.successRate)}</div>
            <div className="muted mono">
              {overview.successes} ok · {overview.failures} fail
            </div>
          </div>
          <div className="metric-card">
            <div className="metric-label">Latency p50 / p95</div>
            <div className="metric-value">
              {overview.p50LatencyMs.toFixed(0)}
              <span className="metric-unit">ms</span>
            </div>
            <div className="muted mono">p95 {overview.p95LatencyMs.toFixed(0)} ms</div>
          </div>
          <div className="metric-card">
            <div className="metric-label">Cost units</div>
            <div className="metric-value">{overview.totalCostUnits.toFixed(3)}</div>
            <div className="muted mono">approx token-derived</div>
          </div>
        </div>
      )}

      <div className="split" style={{ marginTop: "1.25rem" }}>
        <div className="panel">
          <div className="form-section-title">Routes</div>
          {routes.length === 0 && (
            <p className="muted">No samples yet — run a published agent to populate metrics.</p>
          )}
          {routes.map((r) => (
            <button
              key={r.routeName}
              className={selected === r.routeName ? "list-item active" : "list-item"}
              onClick={() => void openRoute(r.routeName)}
            >
              <strong>{r.routeName}</strong>
              <span className="muted">
                {r.requests} req · {pct(r.successRate)} · p95 {r.p95LatencyMs.toFixed(0)}ms
              </span>
            </button>
          ))}
          {selected && (
            <div className="toolbar" style={{ marginTop: "0.75rem" }}>
              <button className="primary" disabled={busy} onClick={() => void tuneSelected()}>
                {busy ? "Tuning…" : "Auto-tune route weights"}
              </button>
            </div>
          )}
        </div>
        <div className="panel">
          <div className="form-section-title">Candidates</div>
          {candidates.map((c) => (
            <div key={c.key} className="metric-bar-row">
              <div className="metric-bar-meta">
                <span className="mono">{c.key}</span>
                <span className="muted">
                  {pct(c.successRate)} · avg {c.avgLatencyMs.toFixed(0)}ms
                </span>
              </div>
              <div className="metric-bar-track">
                <div
                  className="metric-bar-fill"
                  style={{ width: `${Math.max(4, (c.requests / maxReq) * 100)}%` }}
                />
              </div>
            </div>
          ))}
          {detail && <pre className="code" style={{ marginTop: "1rem" }}>{detail}</pre>}
        </div>
      </div>

      <div className="panel" style={{ marginTop: "1.25rem" }}>
        <div className="form-section-title">Recent samples</div>
        <div className="table-like">
          {samples.map((s, i) => (
            <div key={`${s.recordedAt}-${i}`} className="table-row">
              <span className="mono">{s.routeName}</span>
              <span className="mono">
                {s.provider}/{s.model}
              </span>
              <span className={s.success ? "badge ok" : "badge danger"}>
                {s.success ? "ok" : "fail"}
              </span>
              <span>{s.latencyMs.toFixed(1)} ms</span>
              <span className="muted mono">{s.recordedAt}</span>
            </div>
          ))}
          {samples.length === 0 && <p className="muted">No recent samples.</p>}
        </div>
      </div>
    </section>
  );
}
