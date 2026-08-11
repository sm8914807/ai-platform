# Medium-value capabilities (v0.6)

## 1. Inter-agent message bus

`ai_platform/messaging/bus.py` — AMTP-inspired inbox without full federation.

| Endpoint | Description |
|----------|-------------|
| `POST /v1/{ns}/inbox/register` | Register pull/push inbox |
| `POST /v1/{ns}/messages` | Send (idempotent via `idempotency_key`) |
| `GET /v1/{ns}/inbox/{agent}` | Pull pending messages (marks delivered) |
| `POST /v1/messages/{id}/ack` | Acknowledge |
| `GET /v1/{ns}/messages` | List message history |

Delivery modes: **pull** (default) and **push** (webhook).

## 2. Context engineering

`ai_platform/context/engineer.py` — runs before every model call in `AgentEngine`:

- Token budget estimation
- Relevance filtering against current query
- Extractive summarization of older turns
- Hard truncate as last resort

Results appear in execution `done` events under `contextEngineering`.

## 3. Helm / Docker / CI

```
deploy/docker/Dockerfile
deploy/docker/docker-compose.yml
deploy/helm/ai-platform/          # Chart + HPA + PVC + secrets
.github/workflows/ci.yml          # pytest + console tsc + docker build
```

```bash
# Local stack
docker compose -f deploy/docker/docker-compose.yml up --build

# Kubernetes
helm upgrade --install ai-platform deploy/helm/ai-platform \
  --set secrets.openaiApiKey=$OPENAI_API_KEY
```

## 4. Real provider adapters

`ai_platform/model_router/providers.py`:

| Provider | Env |
|----------|-----|
| `openai` | `OPENAI_API_KEY` |
| `anthropic` | `ANTHROPIC_API_KEY` |
| `bedrock` | AWS creds / `BEDROCK_API_KEY` / `BEDROCK_ENDPOINT` |
| `azure` | `AZURE_OPENAI_API_KEY` + `AZURE_OPENAI_ENDPOINT` |
| `mock` | always available |

ModelRoute candidates can now use real providers:

```yaml
candidates:
  - provider: openai
    model: gpt-4o
    weight: 70
  - provider: anthropic
    model: claude-sonnet-4
    weight: 0
    fallback: true
```
