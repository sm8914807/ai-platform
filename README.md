# AI Platform

Enterprise control plane for AI agents — define, publish, run, and govern production agent workflows the way companies run the rest of their software.

Versioned agents, prompts, tools, and policies · capability discovery · durable workflows · decision audit trails · compliance packs · admin console.

---

## Who this is for

Platform / MLOps teams that need to run **many domain agents** (support, research, ops, finance) with:

- versioned configuration (not prompts buried in app code)
- human approvals and policy gates
- model routing and tool sandboxes
- auditability (context graph / precedents)
- a console operators can actually use

---

## Quick start

**Requirements:** Python 3.11+, Node 20+ (for the console)

```bash
# API
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
platform-api
# → http://localhost:8080

# Seed demo catalog (optional; stop the API first for offline seed)
python scripts/seed_offline.py

# Console
cd console && npm install && npm run dev
# → http://localhost:5173
```

Or use the helper:

```bash
./scripts/dev.sh
```

Docker:

```bash
docker compose -f deploy/docker/docker-compose.yml up --build
```

---

## What you get

| Area | What it does |
|------|----------------|
| **Registry & CRDs** | Versioned Agents, Prompts, Tools, Workflows, Policies |
| **Platform Studio** | Admin console — resources, flow maps, discovery, messaging |
| **Workflows** | Durable steps + dynamic goal → plan → run |
| **Discovery** | Route work by capability (`support`, `refund`, …) |
| **Context graph** | Decision traces and precedent search |
| **Secrets & compliance** | Encrypted secrets, HIPAA / PCI / GDPR / SOC2 packs |
| **Federation (AMTP)** | Cross-domain agent messaging |
| **Deploy** | Docker Compose + Helm |

See [Architecture](docs/architecture.md) for how the pieces connect.

---

## SDK usage

```python
import asyncio
from ai_platform import Platform

async def main():
    platform = await Platform.start(
        endpoint="http://localhost:8080",
        namespace="default-org/default-project",
        environment="development",
    )
    result = await platform.run(
        "agents/support-agent",
        input={"message": "Help with billing"},
        stream=True,
    )
    async for event in result.stream:
        print(event.type, event.data)

asyncio.run(main())
```

CLI:

```bash
platform run agents/support-agent --input '{"message":"hello"}'
pytest
```

---

## Repository layout

```
ai_platform/   # Control plane, runtime, SDK
console/       # Platform Studio (React)
schemas/v1/    # CRD JSON Schemas
deploy/        # Docker + Helm
examples/      # Sample resources
scripts/       # Dev + seed helpers
docs/          # Architecture overview
tests/         # Pytest suite
```

---

## Configuration

| Variable | Purpose |
|----------|---------|
| `PLATFORM_DB_PATH` | SQLite registry path (default local `.platform/`) |
| `PLATFORM_DATABASE_URL` / `DATABASE_URL` | Postgres DSN for multi-tenant SaaS |
| `VITE_API_BASE` | Console API base (empty = same-origin / Vite proxy) |

---

## License

MIT — see [LICENSE](LICENSE).
