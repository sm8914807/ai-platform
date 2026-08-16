# Architecture

How the platform fits together for product and platform teams.

For the full LLD, folder map, feature use cases, and maturity matrix, see **[COMPLETE_GUIDE.md](COMPLETE_GUIDE.md)**.

```
┌─────────────┐     publish      ┌──────────────┐
│  CRDs       │ ───────────────► │  Registry    │
│  Agents     │                  │  (SQLite /   │
│  Prompts    │                  │   Postgres)  │
│  Tools      │                  └──────┬───────┘
│  Workflows  │                         │ signed bundles
└─────────────┘                         ▼
                                 ┌──────────────┐
                                 │  Runtime /   │
                                 │  Orchestrator│
                                 └──────┬───────┘
                    ┌───────────────────┼───────────────────┐
                    ▼                   ▼                   ▼
              Discovery           Workflows           Context graph
           (capabilities)      (plan → steps)       (decision audit)
                    │                   │                   │
                    └───────────────────┼───────────────────┘
                                        ▼
                              Agents → Models / Tools
                                        │
                                        ▼
                                   Message bus / AMTP
```

## Control plane

Operators define **versioned resources** (Agents, Prompts, Tools, Workflows, Policies). Publishing compiles them into signed bundles the runtime pulls — application code does not hardcode prompts or tool wiring.

## Runtime path

1. Work arrives (API, workflow trigger, or message).
2. **Discovery** routes by capability when needed.
3. **Workflow engine** runs durable steps (agents, tools, human approval).
4. Agents use **model routes**, **toolboxes**, **guardrails**, and **secrets**.
5. Outcomes land in the **context graph** for audit and precedents.

## Surfaces

| Surface | Audience |
|---------|----------|
| Platform Studio (`console/`) | Ops / platform engineers |
| HTTP API (`/v1/...`) | Services and integrations |
| Python SDK (`ai_platform.Platform`) | Product engineers embedding agents |
| Helm / Docker (`deploy/`) | Infrastructure |

## Data stores

- Default: local SQLite under `.platform/`
- SaaS / multi-tenant: set `PLATFORM_DATABASE_URL` (Postgres)
