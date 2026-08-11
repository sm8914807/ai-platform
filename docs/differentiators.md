# High-value differentiators (v0.5)

Inspired by Agentry — implemented as first-class platform capabilities.

## 1. Context Graph (`ai_platform/context_graph/`)

Organizational decision memory beyond chat history.

| Endpoint | Description |
|----------|-------------|
| `POST /v1/{ns}/traces` | Record a decision trace |
| `GET /v1/{ns}/traces` | List traces |
| `POST /v1/{ns}/traces/precedents` | Query by tags/entities |
| `POST /v1/traces/{from}/link/{to}` | Link traces (precedent, supersedes, …) |

## 2. Dynamic workflows (`ai_platform/workflow/dynamic.py`)

Runtime planner → workflow IR → execute (not only static YAML).

| Endpoint | Description |
|----------|-------------|
| `POST /v1/{ns}/workflows/plan` | Plan from goal + run |
| `GET /v1/workflows/dynamic/{id}` | Inspect run |

Heuristics: research goals → parallel agents; onboarding → approval gates; else single agent.

## 3. Agent discovery (`ai_platform/discovery/`)

Capability registry — route to the best agent for a task.

| Endpoint | Description |
|----------|-------------|
| `POST /v1/{ns}/discovery/register` | Register capabilities |
| `POST /v1/{ns}/discovery/find` | Find by capability |
| `POST /v1/{ns}/discovery/route` | Best match |
| `POST /v1/{ns}/discovery/sync` | Index published Agents |

## 4. Admin Console (`console/`)

React + Vite UI for resources, traces, discovery, dynamic flows, compliance.

```bash
# Terminal 1 — API
platform-api

# Terminal 2 — Console
cd console && npm install && npm run dev
# → http://localhost:5173
```

Vite proxies `/v1` and `/health` to `http://localhost:8080`.

## Migration

`migrations/005_differentiators.sql` — decision_traces, links, agent_capabilities, dynamic_workflows.
