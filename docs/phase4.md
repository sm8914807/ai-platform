# Phase 4 — Scale: Multi-Region, Edge, Compliance, Auto-Tuning

## Modules

| Module | Path | Role |
|--------|------|------|
| Regions | `ai_platform/region/` | Multi-region registry, failover, edge node tracking |
| Edge runtime | `ai_platform/edge/` | Local bundle cache, telemetry-only federation |
| Compliance packs | `ai_platform/compliance/` | HIPAA, PCI, GDPR, SOC2 bundles |
| Metrics | `ai_platform/observability/metrics.py` | Model route latency/cost/success |
| Route tuner | `ai_platform/model_router/tuner.py` | AI SRE auto-weight adjustment |

## API endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/v1/regions` | List control plane regions |
| POST | `/v1/regions` | Register region |
| POST | `/v1/regions/{name}/failover` | Failover primary region |
| POST | `/v1/edge/register` | Register edge runtime node |
| POST | `/v1/edge/{id}/telemetry` | Edge telemetry batch |
| GET | `/v1/compliance/packs` | List compliance packs |
| POST | `/v1/{ns}/compliance/install` | Install HIPAA/PCI/GDPR pack |
| POST | `/v1/{ns}/model-routes/{name}/tune` | Auto-tune route weights |

## Edge runtime

```python
from ai_platform import EdgeRuntime

runtime = await EdgeRuntime.start(
    endpoint="https://api.platform.ai",
    namespace="default-org/default-project",
    region="eu-west-1",
    telemetry_only=True,
    cache_path=".platform/edge-bundle.json",
)
result = await runtime.run("agents/support-agent", {"message": "hello"})
```

Edge nodes cache bundles locally and can operate when control plane is unreachable (stale cache).

## Compliance packs

Built-in packs: `hipaa-baseline`, `pci-baseline`, `gdpr-baseline`, `soc2-baseline`.

```bash
curl -X POST http://localhost:8080/v1/default-org/default-project/compliance/install \
  -H 'Content-Type: application/json' \
  -d '{"packId": "hipaa-baseline"}'
```

## Route auto-tuning

Model router records metrics per completion. Tune endpoint recalculates weights:

```bash
curl -X POST "http://localhost:8080/v1/default-org/default-project/model-routes/gpt-4o-routed/tune?apply=true"
```

Strategies `latencyOptimized` and `costOptimized` bias scoring toward lower latency/cost.

## Multi-region

Default regions seeded: `us-east-1` (primary), `eu-west-1` (standby).
Bundles can be fetched from region-specific endpoints for data residency.

## Database

`migrations/004_phase4.sql` — regions, edge_runtimes, model_route_metrics, route_tuning_runs, compliance_installations.
