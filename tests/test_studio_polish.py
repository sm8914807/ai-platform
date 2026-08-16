"""Studio polish: streaming multi-agent, audit retention, edge telemetry."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient

from ai_platform.api.app import create_app
from ai_platform.api.settings import Settings
from ai_platform.core.models import AuditEvent
from ai_platform.registry.memory import InMemoryRegistryStore


@pytest.mark.asyncio
async def test_audit_purge_retention():
    store = InMemoryRegistryStore()
    old = AuditEvent(
        id="audit_old",
        org_id="default-org",
        actor_id="a",
        action="auth.login",
        resource_ref=None,
        payload={},
        ip=None,
        created_at=datetime.now(UTC) - timedelta(days=120),
    )
    new = AuditEvent(
        id="audit_new",
        org_id="default-org",
        actor_id="a",
        action="auth.login",
        resource_ref=None,
        payload={},
        ip=None,
        created_at=datetime.now(UTC),
    )
    await store.append_audit(old)
    await store.append_audit(new)
    deleted = await store.purge_audit("default-org", retain_days=90)
    assert deleted == 1
    remaining = await store.list_audit("default-org", limit=10)
    assert len(remaining) == 1
    assert remaining[0].id == "audit_new"


@pytest.mark.asyncio
async def test_edge_telemetry_persisted_and_summarized(tmp_path):
    from ai_platform.region.service import RegionService

    svc = RegionService(db_path=str(tmp_path / "reg.db"))
    await svc.migrate()

    node_id = await svc.register_edge_node("ns1", None, None, None, {})
    n = await svc.record_edge_telemetry(
        node_id,
        [
            {"type": "heartbeat", "latencyMs": 12.5, "success": True},
            {"type": "sync", "latencyMs": 40, "success": True},
        ],
    )
    assert n == 2
    events = await svc.list_edge_telemetry(node_id=node_id, limit=10)
    assert len(events) == 2
    assert events[0]["latencyMs"] in {12.5, 40}
    summary = await svc.telemetry_summary(hours=24)
    assert summary["eventCount"] >= 2
    assert summary["nodeCount"] >= 1
    assert isinstance(summary["series"], list)


@pytest.mark.asyncio
async def test_execute_stream_emits_turns(tmp_path, monkeypatch):
    monkeypatch.setenv("PLATFORM_AUTH_REQUIRED", "false")
    monkeypatch.setenv("PLATFORM_ALLOW_DEV_LOGIN", "true")
    settings = Settings(
        db_path=str(tmp_path / "stream.db"),
        auth_required=False,
        allow_dev_login=True,
    )
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            ns = "default-org/default-project"
            for kind, name, spec in [
                (
                    "ModelRoute",
                    "m-stream",
                    {
                        "strategy": "weightedFallback",
                        "candidates": [
                            {
                                "provider": "mock",
                                "model": "mock-1",
                                "weight": 100,
                                "fallback": True,
                            }
                        ],
                    },
                ),
                ("Prompt", "p-stream", {"template": "hi {{ message }}"}),
                (
                    "Agent",
                    "planner-s",
                    {
                        "role": "planner",
                        "modelRef": "models/m-stream",
                        "promptRef": "prompts/p-stream",
                    },
                ),
                (
                    "Agent",
                    "executor-s",
                    {
                        "role": "executor",
                        "modelRef": "models/m-stream",
                        "promptRef": "prompts/p-stream",
                    },
                ),
                (
                    "Agent",
                    "reviewer-s",
                    {
                        "role": "reviewer",
                        "modelRef": "models/m-stream",
                        "promptRef": "prompts/p-stream",
                    },
                ),
                (
                    "Agent",
                    "root-s",
                    {
                        "role": "supervisor",
                        "modelRef": "models/m-stream",
                        "promptRef": "prompts/p-stream",
                        "collaboration": {
                            "pattern": "planner_executor_reviewer",
                            "maxIterations": 1,
                            "agents": {
                                "planner": "agents/planner-s",
                                "executor": "agents/executor-s",
                                "reviewer": "agents/reviewer-s",
                            },
                        },
                    },
                ),
            ]:
                body = {
                    "api_version": "platform.ai/v1",
                    "kind": kind,
                    "metadata": {"name": name, "version": "1.0.0", "namespace": ns},
                    "spec": spec,
                }
                r = await client.put(f"/v1/{ns}/{kind}/{name}/versions/1.0.0", json=body)
                assert r.status_code == 200, r.text
                r = await client.post(
                    f"/v1/{ns}/{kind}/{name}/publish",
                    json={"version": "1.0.0", "principal": "test"},
                )
                assert r.status_code == 200, r.text

            async with client.stream(
                "POST",
                f"/v1/{ns}/execute",
                json={
                    "resource_ref": "agents/root-s",
                    "input": {"message": "stream me"},
                    "multiAgent": True,
                    "stream": True,
                },
            ) as resp:
                assert resp.status_code == 200
                assert "text/event-stream" in resp.headers.get("content-type", "")
                text = ""
                async for chunk in resp.aiter_text():
                    text += chunk
            assert '"type": "turn"' in text or '"type":"turn"' in text
            assert (
                '"type": "done"' in text
                or '"type":"done"' in text
                or '"type": "error"' in text
                or '"type":"error"' in text
            )