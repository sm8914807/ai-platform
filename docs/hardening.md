# Hardening (v0.7)

Closes the highest-impact gaps called out after v0.6:

| Gap | What shipped |
|-----|----------------|
| SQLite-only registry | **Postgres** multi-tenant registry via `PLATFORM_DATABASE_URL` / `DATABASE_URL` |
| Mock-only RAG embeddings | **Embedding providers** — `local` (hash) or `openai` (`text-embedding-3-small`) |
| No secrets manager | **Fernet-encrypted secrets** + one-time leases; tools resolve `secretRef` |
| Unsandboxed tools | **Host allowlist, blocked metadata IPs, timeouts** (`SandboxedToolHost`) |
| Bus ≠ federation | **AMTP federation lite** — domain registry, `agent@domain` send, inbound gateway |
| Thin Admin Console | Console pages for **Messaging, Secrets, Federation** |

## Postgres (SaaS registry)

```bash
# Local SQLite (default)
platform-api

# Multi-tenant Postgres
export PLATFORM_DATABASE_URL=postgresql://platform:platform@localhost:5432/ai_platform
pip install 'ai-platform[postgres]'   # or asyncpg
platform-api
```

Docker Compose includes a Postgres service. Run the API against it:

```bash
cd deploy/docker
docker compose --profile postgres up
# api-postgres on :8081 with PLATFORM_DATABASE_URL set
```

Messaging, context graph, and other aux stores still use the SQLite `PLATFORM_DB_PATH` file in this release. Core resource CRUD / publish path is Postgres when the DSN is set.

## Embeddings

```bash
export PLATFORM_EMBEDDING_PROVIDER=auto   # openai if OPENAI_API_KEY else local
export PLATFORM_EMBEDDING_PROVIDER=openai
export OPENAI_API_KEY=sk-...
export PLATFORM_EMBEDDING_MODEL=text-embedding-3-small
```

`KnowledgeStore` batches embed on ingest and uses hybrid keyword + cosine retrieval.

## Secrets & sandbox

```http
PUT /v1/{ns}/secrets/{name}   {"value":"..."}
GET /v1/{ns}/secrets
POST /v1/{ns}/secrets/{name}/lease
```

Tool configs may set `"secretRef": "secrets/openai-key"`. REST calls are checked against allowlists; link-local metadata hosts are blocked.

## Federation

```http
GET  /.well-known/amtp
GET  /v1/federation/info
POST /v1/federation/peers          {"domain","gateway"}
POST /v1/{ns}/federation/send      {"recipient":"agent@peer.domain", ...}
POST /v1/federation/inbound        # peer gateways POST here
```

Local recipients (`agents/x` or `x@local-domain`) stay on the message bus. Cross-domain addresses require a registered peer.

## Still lighter than Agentry / Retool

- Federation is HTTP forward + discovery, not full AMTP capability negotiation / schema exchange.
- Console is an ops surface, not a full internal-tools product.
- Aux tables (bus, traces, …) are not yet fully on Postgres.
