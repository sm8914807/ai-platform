"""Metrics dashboard — summary, route detail, Prometheus export."""

from __future__ import annotations

from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from ai_platform.api.app import create_app
from ai_platform.api.settings import Settings
from ai_platform.db.sql import create_sql_backend, migrate_aux_stores
from ai_platform.observability.metrics import MetricsCollector


@pytest.mark.asyncio
async def test_metrics_summary_and_prometheus(tmp_path: Path):
    sql = create_sql_backend(db_path=str(tmp_path / "m.db"))
    await migrate_aux_stores(sql)
    metrics = MetricsCollector(sql=sql)
    ns = "ns-1"
    for _ in range(8):
        await metrics.record("models/gpt-4o-routed", ns, "mock", "fast", 12.0, True, 0.01)
    for _ in range(2):
        await metrics.record("models/gpt-4o-routed", ns, "mock", "slow", 180.0, False, 0.05)

    summary = await metrics.summarize_namespace(ns, window=100)
    assert summary["overview"]["requests"] == 10
    assert summary["overview"]["successes"] == 8
    assert summary["overview"]["successRate"] == 0.8
    assert summary["routes"][0]["routeName"] == "models/gpt-4o-routed"
    assert len(summary["candidates"]) == 2

    detail = await metrics.summarize_route("models/gpt-4o-routed", ns)
    assert detail["overview"]["failures"] == 2
    assert len(detail["recent"]) == 10

    prom = await metrics.prometheus_text(ns)
    assert "platform_model_route_requests_total" in prom
    assert 'route="models/gpt-4o-routed"' in prom
    await sql.close()


@pytest.mark.asyncio
async def test_metrics_api_endpoints(tmp_path: Path):
    settings = Settings(db_path=str(tmp_path / "metrics-api.db"), auth_required=False)
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        st = app.state.platform
        ns = "default-org/default-project"
        ns_id = await st.registry.ensure_namespace(ns, "development")
        await st.metrics_collector.record(
            "models/demo", ns_id, "mock", "mock-1", 9.5, True, 0.002
        )
        await st.metrics_collector.record(
            "models/demo", ns_id, "mock", "mock-1", 11.0, True, 0.002
        )

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            summary = await client.get(f"/v1/{ns}/metrics/summary")
            assert summary.status_code == 200, summary.text
            body = summary.json()
            assert body["overview"]["requests"] >= 2
            assert any(r["routeName"] == "models/demo" for r in body["routes"])

            route = await client.get(f"/v1/{ns}/metrics/routes/demo")
            assert route.status_code == 200
            assert route.json()["overview"]["successes"] >= 2

            recent = await client.get(f"/v1/{ns}/metrics/recent")
            assert recent.status_code == 200
            assert len(recent.json()["samples"]) >= 2

            prom = await client.get("/metrics")
            assert prom.status_code == 200
            assert "platform_model_route_requests_total" in prom.text
