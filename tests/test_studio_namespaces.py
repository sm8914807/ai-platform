"""Namespace switcher + unpublish + multi-agent execute flag."""

from __future__ import annotations

from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from ai_platform.api.app import create_app
from ai_platform.api.settings import Settings
from ai_platform.core.models import CollaborationSpec, PlatformResource, ResourceKind, ResourceMetadata
from ai_platform.registry.memory import InMemoryRegistryStore


@pytest.fixture
async def client(tmp_path: Path):
    settings = Settings(db_path=str(tmp_path / "ns.db"), auth_required=False)
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac


@pytest.mark.asyncio
async def test_list_and_ensure_namespaces(client: AsyncClient):
    listed = await client.get("/v1/namespaces")
    assert listed.status_code == 200
    body = listed.json()
    assert body["default"] == "default-org/default-project"
    assert any(n["path"] == "default-org/default-project" for n in body["namespaces"])

    created = await client.post(
        "/v1/namespaces", json={"path": "acme/billing", "environment": "development"}
    )
    assert created.status_code == 200
    assert created.json()["path"] == "acme/billing"

    again = await client.get("/v1/namespaces")
    paths = {n["path"] for n in again.json()["namespaces"]}
    assert "acme/billing" in paths


@pytest.mark.asyncio
async def test_unpublish_resource(client: AsyncClient):
    ns = "default-org/default-project"
    upsert = await client.put(
        f"/v1/{ns}/Prompt/hello-ns/versions/1.0.0",
        json={
            "api_version": "platform.ai/v1",
            "kind": "Prompt",
            "metadata": {"name": "hello-ns", "namespace": ns, "version": "1.0.0"},
            "spec": {"template": "hi"},
        },
    )
    assert upsert.status_code == 200
    pub = await client.post(
        f"/v1/{ns}/Prompt/hello-ns/publish",
        json={"version": "1.0.0"},
    )
    assert pub.status_code == 200
    un = await client.post(f"/v1/{ns}/Prompt/hello-ns/unpublish", json={})
    assert un.status_code == 200
    resources = await client.get(f"/v1/{ns}/resources")
    names = [r["name"] for r in resources.json()["resources"] if r["kind"] == "Prompt"]
    assert "hello-ns" not in names


@pytest.mark.asyncio
async def test_memory_list_namespaces_and_unpublish():
    store = InMemoryRegistryStore()
    ns = await store.ensure_namespace("org/a", "development")
    await store.ensure_namespace("org/b", "staging")
    rows = await store.list_namespaces()
    assert {r["path"] for r in rows} == {"org/a", "org/b"}
    resource = PlatformResource(
        kind=ResourceKind.PROMPT,
        metadata=ResourceMetadata(name="p", namespace="org/a", version="1.0.0"),
        spec={"template": "x"},
    )
    await store.upsert_resource_version(ns, resource)
    await store.publish(ns, ResourceKind.PROMPT, "p", "1.0.0")
    await store.unpublish(ns, ResourceKind.PROMPT, "p")
    assert await store.get_published_version(ns, ResourceKind.PROMPT, "p") is None


def test_collaboration_spec_patterns():
    spec = CollaborationSpec(
        pattern="planner_executor_reviewer",
        agents={
            "planner": "agents/planner-agent",
            "executor": "agents/executor-agent",
            "reviewer": "agents/reviewer-agent",
        },
    )
    assert spec.max_iterations == 3
    assert spec.agents["planner"].startswith("agents/")
