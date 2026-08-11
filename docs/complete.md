# Completeness pass (v0.8)

Closes the three “still lighter than complete” gaps from v0.7.

## 1. Aux stores on Postgres

When `PLATFORM_DATABASE_URL` / `DATABASE_URL` is set:

- Registry **and** aux tables (messaging, traces, discovery, secrets, workflows, regions, metrics, AMTP, …) run on the **same Postgres**.
- Shared `SqlBackend` (`ai_platform/db/sql.py`) with `?` → `$n` rewrite.
- Migration: `migrations/postgres/002_aux.sql` applied via `migrate_aux_stores()`.
- SQLite remains the default for local/dev (`PLATFORM_DB_PATH`).

```bash
export PLATFORM_DATABASE_URL=postgresql://platform:platform@localhost:5432/ai_platform
pip install 'ai-platform[postgres]'
platform-api
# /health → sqlBackend: postgres
```

## 2. AMTP federation (Agentry-aligned)

`ai_platform/federation/amtp.py` implements AMTP 1.0-style gateway behavior:

| Capability | Endpoint / behavior |
|------------|---------------------|
| Send / receive | `POST /v1/messages` (multi-recipient fan-out) |
| Status | `GET /v1/messages/{id}/status` |
| Capabilities | `GET /v1/capabilities`, `GET /v1/capabilities/{domain}` |
| Agent discovery | `GET /v1/discovery/agents` |
| Admin agents | `POST /v1/admin/agents` (`X-Admin-Key`) |
| Schemas | `POST/GET /v1/admin/schemas` |
| DNS TXT helper | `GET /v1/amtp/dns-txt` |
| Discovery | `_amtp.{domain}` TXT parse + TTL cache (optional `dnspython`) |
| Delivery | Local bus + push webhook; remote `POST {gateway}/v1/messages` with retries |
| IDs | UUIDv7 message ids, UUIDv4 / content-hash idempotency |

Publish DNS:

```
_amtp.your.domain. TXT "v=amtp1;gateway=https://api.example:443;auth=none,apikey;..."
```

Dev HTTP gateways: `PLATFORM_AMTP_ALLOW_HTTP=true` (default).

## 3. Admin Console → Studio

`console/` is now **Platform Studio**:

- Top bar + workspace health (version, SQL backend)
- Grouped nav (Build / Runtime / Ops)
- **⌘K command palette**
- **Resource editor** (upsert + publish)
- Inspectors for resources, traces, messages
- AMTP tab (send, agents, DNS TXT)
- Light teal/slate product chrome (not ops-dark-only)

```bash
platform-api
cd console && npm install && npm run dev
```

## Honest remaining edges

- Full DNS in production needs `dnspython` + real TXT records (fallback synthesizes a gateway URL).
- Inter-gateway mTLS / OAuth still advertised, not enforced end-to-end.
- Console is a strong ops product surface — not a full Retool app builder.
