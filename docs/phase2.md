# Phase 2 — Durable Workflows, Memory, Knowledge, Governance

## New modules

| Module | Path | Role |
|--------|------|------|
| Workflow engine | `ai_platform/workflow/` | Checkpointed steps: agent, tool, parallel, humanApproval |
| Memory service | `ai_platform/memory/` | Layered conversation memory + replay |
| Knowledge / RAG | `ai_platform/knowledge/` | Chunking, mock embeddings, hybrid retrieval |
| Policy engine | `ai_platform/policy/` | RBAC/ABAC, fail-closed |
| Guardrails | `ai_platform/guardrails/` | PII mask, injection detection |
| Evaluation | `ai_platform/evaluation/` | Publish gates, golden datasets |
| Promotion | `ai_platform/promotion/` | Environment promotion staging → production |
| Publish gates | `ai_platform/publish/` | Policy + eval before publish |

## New API endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/v1/{ns}/promote` | Request environment promotion |
| POST | `/v1/promotions/{id}/approve` | Approve promotion |
| POST | `/v1/workflows/runs/{id}/approve` | Approve human step |
| POST | `/v1/workflows/runs/{id}/resume` | Resume workflow after approval |

Publish endpoint now supports `evalSuiteRef` and enforces policy + evaluation gates.

## Workflow execution

```python
from ai_platform import Platform

platform = await Platform.start(...)
result = await platform.run(
    "workflows/onboarding-flow",
    input={"message": "new user"},
    stream=True,
)
```

## Memory + RAG in agents

Agents with `memoryRef` and `knowledgeRefs` automatically:
- Load conversation history from memory
- Inject retrieved chunks into prompts
- Persist user/assistant turns after execution

## Database migration

`migrations/002_phase2.sql` adds workflow runs, checkpoints, memory, knowledge chunks, evaluation runs, and promotion tables. Applied automatically on API startup.

## Example resources

See `examples/resources/` for memory profiles, knowledge sources, policies, guardrails, evaluation suites, environments, and workflows.
