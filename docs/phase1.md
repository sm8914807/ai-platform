# Phase 1 — Python Implementation

Phase 1 delivers a **Python-first** stack (`pip install ai-platform`).

## Delivered

### Foundation
- JSON Schema CRDs in `schemas/v1/`
- Pydantic models in `ai_platform/core/models.py`
- SQLite registry + migrations in `migrations/001_initial.sql`
- FastAPI control plane in `ai_platform/api/`
- Ed25519 signed bundles in `ai_platform/bundler/`
- Runtime SDK with `Platform.start()` in `ai_platform/sdk/`

### Execution
- Orchestrator in `ai_platform/orchestrator/`
- Single-agent engine in `ai_platform/agent/`
- Model router with mock provider + fallback in `ai_platform/model_router/`
- Tool host with MCP, OpenAPI, REST adapters in `ai_platform/tool_host/`
- OTLP tracing setup in `ai_platform/telemetry/`

## API endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Health + signing public key |
| POST | `/v1/nodes/register` | Register runtime SDK node |
| PUT | `/v1/{ns}/{kind}/{name}/versions/{ver}` | Upsert resource version |
| POST | `/v1/{ns}/{kind}/{name}/publish` | Publish version |
| GET | `/v1/bundles/{env}` | Fetch signed bundle |
| GET | `/v1/{ns}/{kind}/{name}` | Get published resource |

## Install (when network available)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -e .
pytest
platform-api
```

## Example resources

See `examples/resources/` and `examples/demo_run.py`.
