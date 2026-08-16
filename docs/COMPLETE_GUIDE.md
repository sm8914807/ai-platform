# AI Platform — Complete Product + Technical Guide (LLD)

**Audience:** you (builder/owner), platform engineers, and future contributors who need to understand *what this is*, *how every folder connects*, *how a request flows through code*, and *what is real vs stubbed*.

**Version covered:** platform `0.8.0` (`pyproject.toml`). Console package still reports `0.5.0` — cosmetic mismatch only.

**How to use this doc:** read top-to-bottom once. Then keep §3 (folder map) and §5 (request flows) open while you browse code. Use §12 (maturity matrix) when deciding what to build next.

---

## Table of contents

1. [Product: what this platform is](#1-product-what-this-platform-is)
2. [Company story: how a real org uses it](#2-company-story-how-a-real-org-uses-it)
3. [Repository map (folders → responsibility)](#3-repository-map-folders--responsibility)
4. [High-level architecture](#4-high-level-architecture)
5. [End-to-end request flows (LLD)](#5-end-to-end-request-flows-lld)
6. [CRD catalog — every resource kind](#6-crd-catalog--every-resource-kind)
7. [Subsystem deep dive (code + use case)](#7-subsystem-deep-dive-code--use-case)
8. [Platform Studio (console) map](#8-platform-studio-console-map)
9. [Data stores, migrations, config](#9-data-stores-migrations-config)
10. [Deploy, CLI, SDK](#10-deploy-cli-sdk)
11. [How we built it (construction narrative)](#11-how-we-built-it-construction-narrative)
12. [Maturity matrix — real / partial / missing](#12-maturity-matrix--real--partial--missing)
13. [What you can add next](#13-what-you-can-add-next)
14. [Reading order for the codebase](#14-reading-order-for-the-codebase)

---

## 1. Product: what this platform is

This is an **enterprise control plane for AI agents**.

The core idea (same as Kubernetes for containers, but for agents):

| Traditional app | This platform |
|-----------------|---------------|
| Prompts buried in Python/TS | Versioned **Prompt** CRDs |
| Hardcoded OpenAI client | **ModelRoute** with fallbacks |
| Ad-hoc HTTP tool calls | **Tool** + **Toolbox** + sandbox |
| “Hope the agent is safe” | **Policy** + **Guardrail** + publish gates |
| No audit of decisions | **Context graph** traces + precedents |
| One agent script | **Workflow** + multi-agent patterns + discovery |

**Surfaces:**

| Surface | Path | Who uses it |
|---------|------|-------------|
| HTTP API | `ai_platform/api/app.py` → `:8080` | Services, console, SDK |
| Platform Studio | `console/` → `:5173` | Operators / platform team |
| Python SDK | `ai_platform/sdk/platform.py` | Product engineers |
| CLI | `ai_platform/cli.py` (`platform`, `platform-api`) | DevOps / local runs |
| Schemas | `schemas/v1/*.json` | Validation contract |
| Examples | `examples/resources/*.yaml` | Seed / learning catalog |

**What it is *not* (yet):** a fully authenticated multi-tenant SaaS with every enterprise IdP wired — but JWT auth, durable stores, and production MCP are in place. See §12.

---

## 2. Company story: how a real org uses it

### Example company: “Acme Bank”

Acme wants three agents:

1. **Support agent** — answer billing questions, look up customers.
2. **Refund agent** — propose refunds; human must approve large amounts.
3. **Onboarding workflow** — enrich new user → manager approval → welcome message.

### Day-1 operator workflow (today, with this repo)

```
1. Start API          → platform-api
2. Seed examples      → python scripts/seed_offline.py
3. Start Studio       → cd console && npm run dev
4. Open Resources     → see Agents, Prompts, Tools, …
5. Open Editor        → edit Agent form / JSON → Save → Publish
6. Test agent         → Editor “Test published agent” → POST /execute
7. Watch traces       → Context graph view
8. Register capability→ Discovery: support / refund
9. Plan dynamic flow  → Dynamic workflows: goal → plan → run
10. Store API keys    → Secrets view (encrypted at rest)
```

### What Acme gains

- **Config as data:** change the prompt without redeploying the banking app.
- **Governance:** publish can run policy + evaluation gates (`publish/service.py`).
- **Audit:** every decision can be recorded as a trace (`context_graph/`).
- **Routing:** work titled “refund” can find the refund-capable agent (`discovery/`).
- **HITL:** workflow step `humanApproval` pauses until approve API is called.

### Product personas

| Persona | Job on this platform |
|---------|----------------------|
| Platform engineer | Owns registry, policies, model routes, secrets |
| Agent builder | Authors Agent/Prompt/Tool/Workflow CRDs |
| Compliance officer | Installs HIPAA/PCI packs, reviews audit |
| Product engineer | Calls SDK `platform.run("agents/…")` from apps |
| Ops | Promotes bundles across environments (API exists; UI thin) |

---

## 3. Repository map (folders → responsibility)

```
ai-platform/
├── ai_platform/          # Python package — control plane + runtime
├── console/              # React admin UI (Platform Studio)
├── schemas/v1/           # JSON Schema contracts for CRDs
├── examples/             # Sample YAML resources + demo scripts
├── scripts/              # seed_offline.py, seed_demo.py, helpers
├── migrations/           # SQL schema evolution (SQLite + Postgres)
├── deploy/               # Docker Compose, Dockerfile, Helm, Terraform notes
├── docs/                 # This guide + architecture.md
├── tests/                # Pytest suite (phase1–4, complete, governor, …)
├── pyproject.toml        # Package metadata, deps, entrypoints
└── README.md             # Quick start
```

### `ai_platform/` — one folder per concern

| Folder | Role | Key entry file |
|--------|------|----------------|
| `api/` | FastAPI app, settings, AppState wiring | `app.py`, `state.py`, `settings.py` |
| `core/` | CRD Pydantic models, IDs, validation | `models.py` |
| `registry/` | Persist versioned resources | `store.py`, `sqlite.py`, `postgres.py`, `memory.py` |
| `bundler/` | Compile published resources → signed bundle | `compiler.py` |
| `publish/` | Publish with policy + eval gates | `service.py` |
| `promotion/` | Move published config across environments | `service.py` |
| `agent/` | Single-agent + multi-agent execution | `engine.py`, `multi.py` |
| `orchestrator/` | Top-level: policy → agent/workflow/multi | `engine.py` |
| `workflow/` | Durable steps + dynamic planner | `engine.py`, `dynamic.py`, `store.py` |
| `model_router/` | Provider adapters + routing + auto-tune | `providers.py`, `router.py`, `tuner.py` |
| `tool_host/` | Tool invocation + sandbox | `host.py`, `sandbox.py` |
| `governor/` | Tool rate/quota (Redis optional) | `engine.py` |
| `guardrails/` | PII / injection / moderation pipeline | `pipeline.py` |
| `policy/` | Allow/deny rules on actions | `engine.py` |
| `memory/` | Conversation/session memory backends | `service.py` |
| `knowledge/` | RAG retrieve + embeddings | `service.py`, `embeddings.py` |
| `context/` | Context window budgeting | `engineer.py` |
| `context_graph/` | Decision traces + precedent search | `service.py` |
| `discovery/` | Capability register / find / route | `service.py` |
| `messaging/` | In-process/SQL message bus | `bus.py` |
| `federation/` | Cross-domain gateway + AMTP | `gateway.py`, `amtp.py` |
| `secrets/` | Fernet encrypt + leases | `manager.py` |
| `auth/` | Identity store, SCIM, SSO JWT | `identity.py`, `sso.py` |
| `evaluation/` | Eval suites for publish gates | `runner.py` |
| `compliance/` | HIPAA/PCI/GDPR/SOC2 packs | `packs.py` |
| `marketplace/` | Plugin catalog + install | `service.py` |
| `git_sync/` | Apply/export CRDs from git | `service.py` |
| `terraform/` | Export resources as TF JSON | `export.py` |
| `region/` | Multi-region registry + failover | `service.py` |
| `edge/` | Edge/embedded runtime helper | `runtime.py` |
| `observability/` | Route latency metrics for tuner | `metrics.py` |
| `telemetry/` | OTLP tracing helpers | `tracing.py` |
| `db/` | Shared SQL backend (aiosqlite/asyncpg) | `sql.py` |
| `sdk/` | Client `Platform.start()` / `run()` | `platform.py` |
| `cli.py` | CLI entry | — |

### How folders connect (mental model)

```
console/api.ts  ──HTTP──►  api/app.py  ──uses──►  api/state.py (AppState)
                                                    │
                    ┌───────────────────────────────┼───────────────────────────────┐
                    ▼                               ▼                               ▼
              registry/*                      agent/engine.py                 workflow/*
              publish/*                       model_router/*                  discovery/*
              bundler/*                       tool_host/*                     context_graph/*
              secrets/*                       guardrails/*                    messaging/*
                                              policy/*                        federation/*
```

**Rule of thumb:** anything under `api/` is the *wiring*. Business logic lives in sibling packages. `core/models.py` is the shared language.

---

## 4. High-level architecture

```
┌──────────────────┐     CRUD / publish      ┌─────────────────────┐
│ Platform Studio  │ ───────────────────────►│ Registry            │
│ console/         │                         │ (SQLite | Postgres) │
└────────┬─────────┘                         └──────────┬──────────┘
         │                                              │ publish
         │ execute / discover / secrets                 ▼
         │                                   ┌─────────────────────┐
         │                                   │ BundleCompiler      │
         │                                   │ Ed25519 signed      │
         │                                   └──────────┬──────────┘
         │                                              │ load bundle index
         ▼                                              ▼
┌──────────────────┐                         ┌─────────────────────┐
│ FastAPI /v1/*    │ ───────────────────────►│ Orchestrator        │
└──────────────────┘                         │  ├─ PolicyEngine    │
                                             │  ├─ AgentEngine     │
                                             │  ├─ MultiAgentEngine│
                                             │  └─ WorkflowEngine  │
                                             └──────────┬──────────┘
                    ┌───────────────────────────────────┼────────────────┐
                    ▼                                   ▼                ▼
              ModelRouter                         ToolHost+Sandbox   Guardrails
              (mock/OpenAI/…)                     + ToolGovernor     + Memory/RAG
                    │                                   │
                    └─────────────────┬─────────────────┘
                                      ▼
                              Context graph / Message bus / AMTP
```

### Namespace model

Resources live under a path like:

```
default-org / default-project
```

API: `/v1/{namespace:path}/resources`  
Console constant: `DEFAULT_NS` in `console/src/api.ts`.

Environments (development / staging / production) appear in promotion and bundles (`Environment` CRD + `GET /v1/bundles/{environment}`).

---

## 5. End-to-end request flows (LLD)

### 5.1 Save a resource from Studio

```
EditorView (App.tsx)
  → api.upsertResource(ns, kind, name, version, body)
  → PUT /v1/{ns}/{kind}/{name}/versions/{version}
  → app.upsert_version
  → RegistryStore.upsert_resource_version
  → SQLite/Postgres row for that version (not yet “live”)
```

**Files:** `console/src/App.tsx` (`EditorView`), `console/src/api.ts`, `ai_platform/api/app.py` (`upsert_version`), `ai_platform/registry/sqlite.py` or `postgres.py`.

### 5.2 Publish a resource

```
Editor “Save & publish”
  → POST /v1/{ns}/{kind}/{name}/publish
  → PublishService.publish_with_gates
       1. PolicyEngine.evaluate(action=resource:publish)
       2. optional EvaluationRunner if evalSuiteRef provided
       3. registry.publish (mark published_version)
       4. append audit event
  → BundleCompiler.compile (all published resources in ns/env)
  → signed BundleManifest (hash + Ed25519 signature)
```

**Files:** `publish/service.py`, `policy/engine.py`, `evaluation/runner.py`, `bundler/compiler.py`.

**Use case:** Acme changes `prompts/support-v3`. Until publish, production agents keep the old prompt. After publish + bundle pull, new runs use the new template.

### 5.3 Test / run an agent (Studio button)

```
Editor “Test published agent”
  → POST /v1/{ns}/execute  { resourceRef: "agents/support-agent", input: {...} }
  → app.execute_resource
  → load published bundle into Orchestrator (or AgentEngine path)
  → Orchestrator.execute
       PolicyEngine: agent:run
       if collaboration/supervisor → MultiAgentEngine
       else if agents/* → AgentEngine.execute
       else if workflows/* → WorkflowEngine.run
```

**AgentEngine internal steps** (`agent/engine.py`):

1. Resolve agent doc from bundle (`agents/name` → `Agent:name`).
2. Load prompt, model route, toolbox, memory, knowledge, guardrails by ref.
3. `GuardrailPipeline.run_input` on user text.
4. Optional RAG via `KnowledgeService.retrieve_for_agent`.
5. Optional memory read via `MemoryService`.
6. `ContextEngineer` packs messages into token budget.
7. `ModelRouter.complete` (providers from `model_router/providers.py`).
8. If model requests tools → `ToolGovernor` check → `SandboxedToolHost.invoke`.
9. Guardrail on output; memory write; emit `ExecutionEvent` (`token` / `tool_call` / `done` / `error`).

**Use case:** Support agent receives `{"message":"Where is my order?"}`, calls `tools/get-customer`, returns answer with citations if knowledge refs exist.

### 5.4 Capability discovery + route

```
DiscoveryView register
  → POST /v1/{ns}/discovery/register  { agentRef, capabilities: ["support"] }
  → AgentDiscoveryService.register

Later: POST /v1/{ns}/discovery/route  { capability: "support", … }
  → find online agents with that capability → pick one
```

**Files:** `discovery/service.py`, migration `migrations/005_differentiators.sql`.

**Use case:** Ticket router does not hardcode `agents/support-agent`. It asks discovery for `"billing"` and gets the current published agent address.

### 5.5 Durable workflow with human approval

```
Workflow CRD steps: agent → humanApproval → agent (when approved)
  → WorkflowEngine.run
  → WorkflowStateStore checkpoint each step
  → status = waiting_approval
  → POST /v1/workflows/runs/{run_id}/approve
  → POST /v1/workflows/runs/{run_id}/resume
```

**Files:** `workflow/engine.py`, `workflow/store.py`, example `examples/resources/workflow-onboarding.yaml`.

**Use case:** Refund of $5,000 pauses for manager. Approve API unblocks the final agent step.

### 5.6 Dynamic workflow (goal → plan → run)

```
WorkflowsView
  → POST /v1/{ns}/workflows/plan  { goal: "onboard VIP customer" }
  → DynamicWorkflowEngine LLM planner (ModelRouter) → IR → execute
  → Falls back to heuristic if JSON invalid / mode=heuristic
  → stores IR, can execute via WorkflowEngine
```

**File:** `workflow/dynamic.py`.

### 5.7 Secrets lease for tools

```
PUT /v1/{ns}/secrets/stripe-key  { value: "sk_…" }
  → SecretsManager.put (Fernet encrypt)

Tool config authRef: secrets/stripe-key
  → Sandbox resolves lease → short-lived plaintext only in process
```

**Files:** `secrets/manager.py`, `tool_host/sandbox.py`.

### 5.8 Federation / AMTP message

```
POST /v1/{ns}/federation/send
  → FederationGateway / AMTPGateway
  → MessageBus persist
  → peer /.well-known/amtp discovery
```

**Files:** `federation/amtp.py`, `federation/gateway.py`, `messaging/bus.py`.

**Use case:** Acme’s fraud domain sends a structured task to retail domain’s agent without sharing a single process.

---

## 6. CRD catalog — every resource kind

Defined in `ai_platform/core/models.py` → `ResourceKind`, validated by `schemas/v1/*.json`.

| Kind | Purpose | Example file | Visual form in Studio? |
|------|---------|--------------|------------------------|
| **Agent** | Who runs: role, model, prompt, tools, memory, guardrails | `examples/resources/agent-support.yaml` | Yes |
| **Prompt** | Template + variables | `prompt-support.yaml` | Yes |
| **Tool** | One callable capability (adapter + schemas) | `tool-get-customer.yaml` | Yes |
| **Toolbox** | Group of tools + permissions / approval flags | `toolbox-crm.yaml` | Yes |
| **ModelRoute** | Provider candidates + strategy | `model-route.yaml` | Yes |
| **Workflow** | Ordered durable steps | `workflow-onboarding.yaml` | Yes |
| **Policy** | Allow/deny rules | `policy.yaml` | Yes |
| **Guardrail** | PII / injection / moderation | `guardrail-pii.yaml` | Yes |
| **MemoryProfile** | Memory layers + backends | `memory-profile.yaml` | No (JSON only) |
| **KnowledgeSource** | Docs + retrieval config | `knowledge-source.yaml` | No (JSON only) |
| **EvaluationSuite** | Datasets + gates for publish | `evaluation-suite.yaml` | No |
| **Environment** | Promotion rules / approvers | `environment-production.yaml` | No |
| **Plugin** | Marketplace installable pack | schema `plugin.json` | No |
| **Connector** | External system connector | schema only | No |
| **MCPBinding** | MCP server binding | schema only | No |
| **ApprovalFlow** | Who can approve HITL steps | referenced by workflows | No |
| **Deployment** | Deploy target metadata | schema only | No |

### Reference style inside specs

Agents never embed full tool JSON. They **point**:

```yaml
modelRef: models/gpt-4o-routed
promptRef: prompts/support-v3
toolboxRef: toolboxes/crm-tools
memoryRef: memory/session-default      # optional
knowledgeRefs: [knowledge/faq]         # optional
guardrails: [guardrails/pii-mask]
```

Resolution map lives in `AgentEngine._resolve` (`agent/engine.py`).

### Minimal Agent example (product shape)

```yaml
apiVersion: platform.ai/v1
kind: Agent
metadata:
  name: support-agent
  namespace: default-org/default-project
  version: "1.0.0"
spec:
  role: executor
  modelRef: models/gpt-4o-routed
  promptRef: prompts/support-v3
  toolboxRef: toolboxes/crm-tools
```

### Multi-agent collaboration (product shape)

See `examples/resources/agent-multi-support.yaml` and `agent/multi.py` patterns:

- `planner_executor_reviewer`
- `hierarchical`
- `supervisor_workers`
- `peer_round_robin`

**Use case:** Planner breaks “fix billing + check fraud” into steps; executor calls tools; reviewer checks policy before final answer.

---

## 7. Subsystem deep dive (code + use case)

### 7.1 API + AppState (the wiring board)

- **`api/settings.py`** — `PLATFORM_*` env vars (db path, Postgres URL, secrets key, redis, federation domain).
- **`api/state.py`** — constructs every service once: registry, publish, promotion, discovery, secrets, agent engine, workflows, AMTP, compliance, …
- **`api/app.py`** — FastAPI routes; lifespan migrates DB; CORS currently `*`.

**Auth today:** `_auth_principal` reads `Authorization` but falls back to `"anonymous"` — **not a hard gate**. Console does not send tokens.

**Use case (intended):** IdP login → JWT → every mutate/execute call carries principal for policy + audit.

### 7.2 Registry

- Interface: `registry/store.py`
- Impls: `sqlite.py` (default), `postgres.py` (SaaS), `memory.py` (tests)

Stores: resources, versions, published pointer, audit events, namespaces.

**Use case:** GitOps-like history of every prompt version with author + commit message fields.

### 7.3 Bundler

`bundler/compiler.py` — sorts published resources, hashes payload, signs with Ed25519, returns `BundleManifest`.

**Use case:** Edge/runtime nodes pull `GET /v1/bundles/{environment}` and verify signature before executing (edge helpers in `edge/runtime.py`).

### 7.4 Policy engine

`policy/engine.py` — loads Policy CRDs from bundle; `evaluate(PolicyContext)` → allow/deny.

Used on: publish, agent run (orchestrator).

**Use case:** Deny `agent:run` on `agents/refund-agent` in `development` for principal `contractor`.

### 7.5 Guardrails

`guardrails/pipeline.py` — types: `pii_mask`, `injection_detect`, `content_moderation`, `custom`.

**Use case:** Mask card numbers before they hit the model; block jailbreak patterns on input.

### 7.6 Model router

- `providers.py` — Mock + OpenAI + Anthropic + Bedrock (+ optional Azure) HTTP adapters.
- `router.py` — strategies: weightedFallback, costOptimized, latencyOptimized, capabilityMatch.
- `tuner.py` — reweights candidates from `observability/metrics.py`.

**Default:** mock provider always registered; real providers need API keys.

**Use case:** Prefer GPT-4o; if latency > threshold, fall back to cheaper model. Nightly tune adjusts weights from metrics.

### 7.7 Tools + sandbox + governor

- `tool_host/mcp/` — production MCP client: **stdio** subprocess + **Streamable HTTP** JSON-RPC (`initialize`, `tools/list`, `tools/call`).
- `tool_host/host.py` — `MCPToolAdapter` (live or mock when only `server:` is set).
- `tool_host/sandbox.py` — URL/command allowlists, secret injection, timeouts.
- `governor/engine.py` — Redis-backed quotas (`PLATFORM_REDIS_URL`) or in-memory.

**API:** `POST /v1/{ns}/mcp/list`, `POST /v1/{ns}/mcp/call`

**Use case:** CRM tool CRD points at an MCP server (`transport: http` + URL, or `stdio` + `npx`/`python3`). Agent calls `get-customer`; sandbox resolves `secrets/mcp-token` and blocks non-allowlisted binaries.

### 7.8 Memory + knowledge

- `memory/service.py` — `SqlMemoryBackend` (default via AppState) on `memory_entries`; `InMemoryBackend` still available for unit tests.
- `knowledge/service.py` + `embeddings.py` — durable `knowledge_chunks` + embeddings JSON; hybrid keyword/vector retrieve. Embeddings: local hash or OpenAI (`PLATFORM_EMBEDDING_PROVIDER`).

**Use case:** Agent remembers prior ticket after API restart; FAQ knowledge source cites policy paragraphs from the SQL index.

### 7.9 Context graph

`context_graph/service.py` — create/list traces, link traces, search precedents.

**Use case:** “Have we refunded this merchant before?” → precedent search before auto-approving.

### 7.10 Discovery

`discovery/service.py` — register capabilities, find, route, sync from published agents.

**Use case:** New “KYC agent” publishes and registers `["kyc","identity"]`; onboarding workflow routes by capability instead of hardcoded ref.

### 7.11 Messaging + federation

- Local bus: `messaging/bus.py` (inbox register, send, ack).
- Cross-domain: `federation/gateway.py`, `federation/amtp.py` (DNS TXT / well-known).

**Use case:** Partner bank’s agent domain receives a signed task envelope.

### 7.12 Secrets

`secrets/manager.py` — Fernet at rest; list metadata only; lease issues short-lived resolve.

**Use case:** Rotate OpenAI key in Studio without editing Agent YAML.

### 7.13 Auth / SCIM / SSO

- `auth/identity.py` — users (SQLite / Postgres).
- `auth/sso.py` — platform session JWT (HMAC) after login.
- `auth/oidc_provider.py` — **real OIDC**: discovery, PKCE authorize, token exchange, JWKS RS256 ID-token validation (Okta / Azure AD / Keycloak).
- Routes: `GET /v1/auth/config`, `POST /v1/auth/login` (dev), `POST /v1/auth/oidc/start`, `POST /v1/auth/oidc/callback`, `/scim/v2/Users*`.
- Studio: IdP button when `PLATFORM_OIDC_ISSUER` + `PLATFORM_OIDC_CLIENT_ID` are set; set `PLATFORM_ALLOW_DEV_LOGIN=false` to disable email login in production.

**Use case:** Okta / Entra ID signs the user in; API issues a short-lived platform Bearer JWT for Studio and SCIM stays gated by the same auth middleware.

### 7.14 Evaluation + publish gates

`evaluation/runner.py` + `evaluation/judges.py` — typed judges run golden datasets:

| Judge | Metric | Behavior |
|-------|--------|----------|
| `keyword_match` | `keyword` | Expected substrings / keywords vs agent output |
| `exact_match` | `exact_match` | Strict string equality |
| `tool_accuracy` | `tool_accuracy` | Expected tools vs tools used |
| `latency` / `cost` | `latency` / `cost` | Live execution budgets |
| `llm_judge` / `faithfulness` / `relevance` / `safety` | criteria name | LLM returns `{"score","rationale"}` |

**Publish path:** suites whose `triggers.onPublish` includes the resource (or explicit `evalSuiteRef`) run before the version is marked published; failure → `403 evaluation_failed`.

**APIs:** `POST /v1/{ns}/evaluations/run`, `GET /v1/{ns}/evaluations/recent`. Studio: Ops → Evaluations + editor “Run evaluation”.

**Use case:** Block publish of support agent if faithfulness / keyword score &lt; gate on golden set.

### 7.15 Promotion

`promotion/service.py` + `POST .../promote` + `POST /v1/promotions/{id}/approve`.

**Use case:** Promote signed bundle from staging → production with two approvers (`Environment.requireApproval`).

### 7.16 Compliance packs

`compliance/packs.py` — HIPAA, PCI, GDPR, SOC2, ISO27001 templates installable into a namespace.

**Use case:** One click installs baseline policies/guardrails for PHI workloads (API install exists; Studio list-only).

### 7.17 Marketplace + git sync + terraform

- `marketplace/service.py` — plugin catalog tiers; install copies resources into registry.
- `git_sync/service.py` — apply YAML from directory / export; `GET .../git-sync/repos` lists sync history.
- `terraform/export.py` — HCL + terraform-json (`build_terraform_files` / `write_terraform_files`).
- Studio **Ops → Git sync** — sync `examples/resources` (or any API-local path), export YAML, view registered repos.
- Studio **Ops → Terraform** — live preview of generated `.tf` / `exported.json`, write to disk.

**Use case:** Platform team exports prompts as Terraform for regulated change management; community plugin “Slack notifier” installs Tool+Agent stubs.

### 7.18 Regions + edge

- `region/service.py` — register regions, set primary, failover, edge node registry + telemetry.
- `edge/runtime.py` — embedded/remote/edge modes, bundle cache.
- Studio **Ops → Regions & edge** — list/register regions, make primary / failover, register edge nodes.
- APIs: `GET/POST /v1/regions`, `POST /v1/regions/{name}/primary|failover`, `GET /v1/edge/nodes`, `POST /v1/edge/register`.

**Use case:** EU residency region stays primary for EU tenants; US standby; edge nodes pull signed bundles.

### 7.19 HITL run inbox

- Durable `pendingApproval` on workflow checkpoints (survives API restart).
- `GET /v1/workflows/inbox` lists `waiting_approval` runs; `GET /v1/workflows/runs/{id}` for detail.
- Studio **Runtime → HITL inbox** — approve & resume or reject; Dynamic flows keeps a quick panel + link.

### 7.20 Observability / telemetry

- `observability/metrics.py` — record + namespace/route summaries (p50/p95, success rate, cost) + Prometheus text.
- Studio **Metrics** view — overview cards, routes, candidates, recent samples, auto-tune button.
- `GET /metrics` — Prometheus scrape (public).
- `GET /v1/{ns}/metrics/summary|routes|recent` — JSON for Studio.
- `telemetry/tracing.py` — **OTLP on API lifespan**: `setup_tracing` / `shutdown_tracing`, HTTP server spans, `platform.execute` + orchestrator spans.
  - Env: `PLATFORM_OTLP_ENDPOINT=http://localhost:4318/v1/traces` (also honors `OTEL_EXPORTER_OTLP_*`).
  - `GET /health` reports `tracing.enabled` and `otlpEndpointConfigured`.

**Use case:** Ops sees p95 latency spike on `models/gpt-4o-routed`, drills into candidates, runs auto-tune to reweight; traces land in Jaeger/Grafana Tempo via OTLP.

---

## 8. Platform Studio (console) map

**Stack:** React 18 + Vite 5 + TypeScript. Entry: `console/src/main.tsx` → `App.tsx`.

| View id | Nav label | What it does | Backend calls |
|---------|-----------|--------------|---------------|
| `overview` | Overview | Health + architecture diagram | `/health` |
| `maps` | Flow maps | Platform / agent / messaging diagrams | resources list |
| `resources` | Resources | Catalog of CRDs | `GET .../resources` |
| `editor` | Resource editor | Forms + JSON + publish + agent test | upsert, publish, execute |
| `workflows` | Dynamic flows | Goal plan & run + quick HITL | `.../workflows/plan`, approve/resume |
| `collaboration` | Multi-agent | Role wiring, timeline, diagnosis | execute + collaboration override |
| `traces` | Context graph | Traces + precedents | `.../traces*` |
| `discovery` | Discovery | List agents + register capabilities | discovery/* |
| `messaging` | Message bus | Inbox / messages | messages/* |
| `hitl` | HITL inbox | Waiting approvals · approve/reject/resume | `/v1/workflows/inbox`, runs/* |
| `federation` | AMTP federation | Peers / send | federation/* |
| `regions` | Regions & edge | Regions, failover, edge nodes | `/v1/regions`, `/v1/edge/*` |
| `secrets` | Secrets | Create/list/delete | secrets/* |
| `compliance` | Compliance | List + install packs | compliance/* |
| `metrics` | Metrics | Route latency / cost / tune | metrics/* |
| `evaluations` | Evaluations | Suites + publish gates | evaluation/* |
| `git` | Git sync | Sync/export YAML | git/* |
| `terraform` | Terraform | Preview + write | terraform/* |

**Forms:** `console/src/resourceForms.tsx` — Agent, Prompt, Tool, Toolbox, Workflow, ModelRoute, Policy, Guardrail.

**Diagrams:** `console/src/diagrams.tsx`.

**API client:** `console/src/api.ts` — no auth header yet.

---

## 9. Data stores, migrations, config

### Defaults (local)

| Concern | Default location |
|---------|------------------|
| Registry | `.platform/registry.db` (SQLite) |
| Workflows / aux | same SQL backend / related sqlite files |
| Secrets key | `PLATFORM_SECRETS_KEY` or weak dev default |

### Postgres (SaaS mode)

Set `PLATFORM_DATABASE_URL` (or `DATABASE_URL`). Registry uses `registry/postgres.py`. Shared helpers in `db/sql.py`.

Migrations under `migrations/` and `migrations/postgres/`.

### Important env vars (`api/settings.py`)

| Variable | Meaning |
|----------|---------|
| `PLATFORM_DB_PATH` | SQLite path |
| `PLATFORM_DATABASE_URL` | Postgres DSN |
| `PLATFORM_SECRETS_KEY` | Fernet master |
| `PLATFORM_REDIS_URL` | Tool governor |
| `PLATFORM_FEDERATION_DOMAIN` | AMTP domain |
| `PLATFORM_EMBEDDING_PROVIDER` | auto \| local \| openai |
| `PLATFORM_API_HOST` / `PORT` | Bind address |

### Console

`VITE_API_BASE` — leave empty when Vite proxies to API; set absolute URL if split hosts.

---

## 10. Deploy, CLI, SDK

### Local

```bash
pip install -e ".[dev]"
platform-api                 # :8080
python scripts/seed_offline.py
cd console && npm i && npm run dev   # :5173
```

### Docker / Helm

- `deploy/docker/` — Compose + Dockerfile
- `deploy/helm/ai-platform/` — Deployment, Service, HPA, PVC, Secret

### CLI

```bash
platform run agents/support-agent --input '{"message":"hello"}'
```

### SDK

```python
from ai_platform import Platform

platform = await Platform.start(
    endpoint="http://localhost:8080",
    namespace="default-org/default-project",
    environment="development",
)
result = await platform.run("agents/support-agent", input={"message": "Help"}, stream=True)
```

Entrypoint wiring: `pyproject.toml` → `platform-api`, `platform`.

---

## 11. How we built it (construction narrative)

This repo grew in layers (visible in `tests/test_phase1.py` … `test_phase4.py` and module comments):

| Layer | What was built | Evidence in tree |
|-------|----------------|------------------|
| **Core CRDs + registry** | Versioned resources, SQLite store, schemas | `core/`, `registry/`, `schemas/` |
| **Runtime** | Agent engine, model router, tools | `agent/`, `model_router/`, `tool_host/` |
| **Governance** | Policy, guardrails, publish gates, eval | `policy/`, `guardrails/`, `publish/`, `evaluation/` |
| **Orchestration** | Workflows, multi-agent, dynamic planner | `workflow/`, `agent/multi.py`, `orchestrator/` |
| **Differentiators** | Discovery, context graph, messaging | `discovery/`, `context_graph/`, `messaging/` |
| **Enterprise ops** | Secrets, compliance, promotion, marketplace, git, TF | respective packages |
| **Scale / edge** | Regions, AMTP federation, governor, Postgres | `region/`, `federation/`, `governor/`, `registry/postgres.py` |
| **Studio** | React console, forms, diagrams, test runner | `console/` |

**Design pattern used everywhere:**

1. Define **spec** in `core/models.py` + `schemas/v1`.
2. Persist via **registry**.
3. **Publish** → **bundle**.
4. Runtime **resolves refs** from bundle (no hardcoding).
5. Expose HTTP in `api/app.py`.
6. Optionally add Studio view + `api.ts` client.

That is the extension recipe for anything new.

---

## 12. Maturity matrix — real / partial / missing

Use this when you read online about “agent platforms” and ask: *do we already have this?*

| Feature | Code status | Studio | Notes |
|---------|-------------|--------|-------|
| CRD registry + versioning | **Real** | Yes | SQLite/Postgres |
| Publish + signed bundles | **Real** | Yes | Ed25519 |
| Agent execute path | **Real** | Yes (test button) | Mock model by default |
| Multi-agent patterns | **Real** | Yes (Build → Multi-agent) | Role wiring, turn timeline, failure diagnosis |
| Workflows + HITL APIs | **Real** | Yes (Runtime → HITL inbox) | Approve/resume + durable pending metadata; inbox lists waiting runs |
| Dynamic plan | **Real (LLM + fallback)** | Yes | LLM via ModelRouter; heuristic if parse fails |
| Discovery | **Real** | Yes | Register + list |
| Context graph | **Real** | Yes | |
| Secrets | **Real** | Yes | Dev key default |
| Policies / guardrails | **Real** | Forms yes | Enforced on publish, agent/workflow run, tool:invoke, MCP call; injection block hard-stops |
| Model providers (OpenAI/…) | **Real adapters** | Form yes | Needs keys; mock default |
| MCP tools | **Real (stdio + HTTP)** | Tool form | Sandboxed; `/v1/.../mcp/list` + `/mcp/call` |
| **Auth enforced** | **Real (default on)** | Login screen | JWT middleware; set `PLATFORM_AUTH_REQUIRED=false` only for local tests |
| Production hardening | **Real** | — | `PLATFORM_ENV=production` refuses weak secrets, open dev-login, memory-only governor |
| Real OIDC (Okta/Azure AD) | **Real** | IdP button | Discovery + PKCE + JWKS; platform session JWT after callback |
| SCIM secure | **Real when auth on** | None | Same Bearer gate as `/v1` |
| Identity on Postgres | **Real (SqlBackend)** | Login | Same tables as SQLite; `IdentityStore(sql=…)` |
| Knowledge / memory durable | **Real (SqlBackend)** | Forms | `memory_entries` + `knowledge_chunks`; wired into AgentEngine |
| Promotion UI | **Real** | Yes | Promote + approve |
| Marketplace UI | **Real** | Yes | List + install |
| Git / Terraform UI | **Real** | Yes (Ops → Git sync / Terraform) | Sync/export YAML; TF preview + write |
| Compliance install UI | **Real** | Yes | Install into namespace |
| Metrics dashboard | **Real** | Yes (Ops → Metrics) | Summary + routes + Prometheus `/metrics` |
| Activity / audit log | **Real** | Yes (Ops → Activity) | `GET /v1/{ns}/audit`; login / publish / secrets / promote |
| OTLP on API | **Real** | — | Lifespan setup/shutdown; HTTP + execute spans; OTLP/HTTP export |
| Eval judge quality | **Real** | Yes (Ops → Evaluations) | Keyword, latency, tool, LLM judges; publish auto-triggers |
| Multi-namespace switcher | **Real** | Yes (topbar) | Persist NS; ensure + list APIs |
| Multi-agent collaboration UI | **Real** | Yes (Build → Multi-agent) | Role wiring + timeline + diagnosis |
| Resource actions | **Real** | Yes | New / Clone / Edit / Unpublish (not hard-delete) |
| Knowledge / Memory / Environment / Eval forms | **Real** | Yes | Editor visual forms |
| CI Postgres + console e2e | **Real** | — | `test-postgres` + `console-e2e` (HITL approve/resume, SCIM UI, OIDC login UI) |
| Regions / edge Studio | **Real** | Yes (Ops → Regions & edge) | Register, primary, failover, edge nodes |
| Tool governor (multi-instance) | **Real** | — | Redis when `PLATFORM_REDIS_URL` set; `PLATFORM_GOVERNOR_BACKEND=auto\|memory\|redis` |
| SCIM admin UI | **Real** | Yes (Ops → Identity) | List / create / deactivate via `/scim/v2/Users` |

---

## 13. What you can add next

Ordered by remaining leverage:

1. Hard resource delete (optional) — skipped by product choice.
2. More audit producers (unpublish, MCP call, execute) and retention policies.
3. Managed Redis / OIDC examples for a one-command SaaS deploy profile.

**Recently shipped:** SCIM Identity Studio UI; richer HITL + OIDC Playwright e2e; production auth/ops hardening; Redis multi-instance governor; Studio Activity / audit log; deeper multi-agent Studio; end-to-end policy/guardrail enforcement; regions/edge; HITL inbox; OIDC; OTLP.

---

## 14. Reading order for the codebase

If you want to *own* this mentally, read in this order:

1. `ai_platform/core/models.py` — language of the platform  
2. `examples/resources/*.yaml` — concrete instances  
3. `ai_platform/api/state.py` — what gets constructed  
4. `ai_platform/api/app.py` — HTTP surface  
5. `ai_platform/registry/store.py` + `sqlite.py` — persistence  
6. `ai_platform/publish/service.py` + `bundler/compiler.py` — go-live path  
7. `ai_platform/orchestrator/engine.py` → `agent/engine.py` → `workflow/engine.py`  
8. `ai_platform/discovery/service.py` + `context_graph/service.py`  
9. `console/src/api.ts` + `App.tsx` (EditorView, DiscoveryView)  
10. `tests/test_complete.py` — behavioral contract of “done” features  

---

## Appendix A — API route index (quick)

| Area | Methods (prefix `/v1` unless noted) |
|------|-------------------------------------|
| Health | `GET /health` |
| Resources | `GET .../resources`, `GET .../{kind}/{name}`, `PUT .../versions/{version}`, `POST .../publish` |
| Execute | `POST .../execute` |
| Workflows | `POST .../workflows/plan`, `GET /workflows/dynamic/{id}`, `POST /workflows/runs/{id}/approve\|resume` |
| Discovery | `POST .../discovery/register\|find\|route\|sync`, `GET .../discovery/agents` |
| Traces | `POST/GET .../traces`, precedents, links |
| Secrets | `PUT/GET/DELETE .../secrets`, `POST .../lease` |
| Messaging | inbox, messages, ack |
| Federation / AMTP | peers, send, inbound, well-known |
| Promote | `POST .../promote`, `POST /promotions/{id}/approve` |
| Marketplace | plugins list/publish, install |
| Git / TF | `POST .../git-sync`, `GET .../git-sync/repos`, `git-export`, `GET .../terraform/preview`, `POST .../terraform/export` |
| Compliance | `GET /compliance/packs`, `POST .../compliance/install` |
| Regions / edge | `/regions`, `/edge/nodes`, `/edge/register`, `/regions/{name}/primary|failover` |
| HITL inbox | `/workflows/inbox`, `/workflows/runs/{id}`, approve/resume |
| Auth / SCIM | `POST /auth/login`, `/scim/v2/Users` |
| Bundles | `GET /bundles/{environment}` |
| Tune | `POST .../model-routes/{name}/tune` |

---

## Appendix B — Test map

| File | Focus |
|------|--------|
| `tests/test_phase1.py` | Core registry / early runtime |
| `tests/test_phase2.py` | Memory/knowledge/policy era |
| `tests/test_phase3.py` | Multi-agent, git sync, terraform |
| `tests/test_phase4.py` | Regions / compliance / scale features |
| `tests/test_complete.py` | Studio-oriented HTTP (incl. execute + discovery) |
| `tests/test_differentiators.py` | Discovery / graph / messaging |
| `tests/test_hardening.py` | Secrets / federation hardening |
| `tests/test_governor.py` | Tool governor |
| `tests/test_medium.py` | Mid-tier scenarios |
| `tests/test_oidc.py` | OIDC JWKS validation + SSO callback |
| `tests/test_studio_namespaces.py` | Namespace APIs + unpublish |
| `tests/test_evaluation.py` | Eval judges + publish gates |
| `console/e2e/studio.spec.ts` | Playwright Studio login + nav |

CI: `.github/workflows/ci.yml` — pytest, **Postgres service job**, console build, **Playwright e2e**, Docker build.

---

*This document describes the system as implemented in this repository. When code and this guide disagree, trust the code — then update this guide.*
