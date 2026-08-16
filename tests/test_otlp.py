"""OTLP / OpenTelemetry API lifespan + HTTP span tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from ai_platform.api.app import create_app
from ai_platform.api.settings import Settings
from ai_platform.telemetry.tracing import (
    clear_memory_spans,
    memory_spans,
    setup_tracing,
    shutdown_tracing,
    tracing_status,
)


@pytest.fixture(autouse=True)
def _reset_tracing():
    shutdown_tracing()
    yield
    shutdown_tracing()


def test_setup_tracing_memory_exporter():
    setup_tracing("test-svc", memory=True, force=True, environment="test")
    status = tracing_status()
    assert status["enabled"] is True
    assert status["memoryExporter"] is True
    tracer = __import__("ai_platform.telemetry.tracing", fromlist=["get_tracer"]).get_tracer(
        "unit"
    )
    with tracer.start_as_current_span("unit.work") as span:
        span.set_attribute("ok", True)
    spans = memory_spans()
    assert any(s.name == "unit.work" for s in spans)
    clear_memory_spans()
    assert memory_spans() == []


@pytest.mark.asyncio
async def test_api_lifespan_emits_http_spans(tmp_path: Path):
    settings = Settings(
        db_path=str(tmp_path / "otel.db"),
        auth_required=False,
        otlp_memory=True,
        otlp_service_name="ai-platform-api-test",
    )
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        clear_memory_spans()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            health = await ac.get("/health")
            assert health.status_code == 200
            body = health.json()
            assert body["tracing"]["enabled"] is True
            assert body["otlpEndpointConfigured"] is False

            await ac.get("/v1/namespaces")

        names = [s.name for s in memory_spans()]
        assert any(n.startswith("GET /health") for n in names)
        assert any("namespaces" in n for n in names)
