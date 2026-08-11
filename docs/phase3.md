# Phase 3 — Enterprise: Multi-Agent, Marketplace, Git Sync, SSO/SCIM, Terraform

## Modules

| Module | Path | Role |
|--------|------|------|
| Multi-agent | `ai_platform/agent/multi.py` | planner/executor/reviewer, supervisor, peer patterns |
| Marketplace | `ai_platform/marketplace/` | Plugin catalog + install into namespace |
| Git sync | `ai_platform/git_sync/` | Apply/export YAML resources |
| Identity | `ai_platform/auth/identity.py` | Users, teams, SCIM 2.0 |
| SSO | `ai_platform/auth/sso.py` | JWT login + Bearer auth |
| Terraform | `ai_platform/terraform/export.py` | HCL/JSON export |

## API endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/v1/auth/login` | Dev SSO login → JWT |
| GET | `/v1/marketplace/plugins` | List marketplace plugins |
| POST | `/v1/marketplace/plugins` | Publish plugin to catalog |
| POST | `/v1/{ns}/marketplace/install` | Install plugin into namespace |
| POST | `/v1/{ns}/git-sync` | Apply YAML directory to registry |
| POST | `/v1/{ns}/git-export` | Export published resources to directory |
| GET | `/scim/v2/Users` | SCIM list users |
| POST | `/scim/v2/Users` | SCIM provision user |
| DELETE | `/scim/v2/Users/{id}` | SCIM deactivate user |
| POST | `/v1/{ns}/terraform/export` | Generate Terraform files |

## Multi-agent usage

Agents with `spec.collaboration` auto-run multi-agent patterns:

```yaml
spec:
  collaboration:
    pattern: planner_executor_reviewer
    maxIterations: 2
    agents:
      planner: agents/planner-agent
      executor: agents/executor-agent
      reviewer: agents/reviewer-agent
```

Or force via orchestrator: `multi_agent=True`.

## CLI

```bash
platform login --email user@company.com
platform apply -f ./examples/resources/
platform tf-export --output ./deploy/terraform/generated
platform run agents/multi-support-agent --input '{"message":"help"}'
```

## SSO

Authenticate API calls with `Authorization: Bearer <token>` from `/v1/auth/login`.
Principal is derived from teams for policy evaluation.

## Database

`migrations/003_phase3.sql` — marketplace, git sync metadata, identity, SSO sessions.
