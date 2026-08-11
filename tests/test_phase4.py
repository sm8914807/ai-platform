"""Phase 4 tests — multi-region, edge, compliance, route tuning."""

import pytest
import tempfile
from pathlib import Path

from ai_platform.compliance.packs import CompliancePackService, BUILTIN_PACKS
from ai_platform.core.models import ModelCandidate, ModelRouteSpec
from ai_platform.edge.runtime import EdgeRuntime
from ai_platform.model_router.tuner import RouteTuner
from ai_platform.observability.metrics import MetricsCollector
from ai_platform.region.service import RegionService
from ai_platform.registry.memory import InMemoryRegistryStore


@pytest.mark.asyncio
async def test_region_service_failover():
    db = tempfile.mktemp(suffix=".db")
    svc = RegionService(db)
    await svc.migrate()
    regions = await svc.list_regions()
    assert len(regions) >= 2
    primary = await svc.get_primary()
    assert primary is not None
    backup = await svc.failover(primary.name)
    assert backup is not None
    assert backup.name != primary.name


@pytest.mark.asyncio
async def test_route_auto_tuning():
    db = tempfile.mktemp(suffix=".db")
    migration = Path(__file__).parent.parent / "migrations" / "004_phase4.sql"
    import aiosqlite

    conn = await aiosqlite.connect(db)
    await conn.executescript(migration.read_text())
    await conn.commit()
    await conn.close()

    metrics = MetricsCollector(db)
    tuner = RouteTuner(metrics, db)
    ns = "ns-test"
    route_name = "models/gpt-4o-routed"
    for _ in range(10):
        await metrics.record(route_name, ns, "mock", "fast-model", 5.0, True, 0.01)
    for _ in range(10):
        await metrics.record(route_name, ns, "mock", "slow-model", 200.0, True, 0.05)

    spec = ModelRouteSpec(
        strategy="latencyOptimized",
        candidates=[
            ModelCandidate(provider="mock", model="fast-model", weight=50),
            ModelCandidate(provider="mock", model="slow-model", weight=50),
        ],
    )
    result = await tuner.tune(route_name, ns, spec)
    assert result.new_weights["mock:fast-model"] >= result.new_weights["mock:slow-model"]


@pytest.mark.asyncio
async def test_compliance_pack_install():
    store = InMemoryRegistryStore()
    db = tempfile.mktemp(suffix=".db")
    import aiosqlite

    conn = await aiosqlite.connect(db)
    migration = Path(__file__).parent.parent / "migrations" / "004_phase4.sql"
    await conn.executescript(migration.read_text())
    await conn.commit()
    await conn.close()

    svc = CompliancePackService(store, db)
    assert len(svc.list_packs()) == len(BUILTIN_PACKS)
    ns = await store.ensure_namespace("org/project", "production")
    result = await svc.install_pack("hipaa-baseline", ns, "org/project", "admin")
    assert result["framework"] == "HIPAA"
    assert len(result["resources"]) >= 2


@pytest.mark.asyncio
async def test_edge_runtime_local_cache(tmp_path):
    cache = tmp_path / "bundle.cache.json"
    cache.write_text(
        '{"resources": [{"kind": "Prompt", "name": "p", "spec": {"template": "cached"}}]}'
    )
    from ai_platform.orchestrator.engine import Orchestrator
    from ai_platform.sdk.platform import PlatformConfig
    from ai_platform.core.models import EdgeRuntimeConfig

    config = PlatformConfig(endpoint="http://invalid:9999", namespace="org/project")
    edge_config = EdgeRuntimeConfig(bundle_cache_path=str(cache))
    runtime = EdgeRuntime(config, edge_config, Orchestrator())
    await runtime._load_cache()
    assert "org/project:development" in runtime.orchestrator._bundle_index
