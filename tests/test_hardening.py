"""v0.7 — Postgres registry, real embeddings, secrets, sandbox, AMTP federation."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from ai_platform.api.app import create_app
from ai_platform.api.settings import Settings
from ai_platform.core.models import KnowledgeSourceSpec
from ai_platform.federation.gateway import (
    FederationGateway,
    parse_agent_address,
)
from ai_platform.knowledge.embeddings import LocalHashEmbedding, build_embedding_provider
from ai_platform.knowledge.service import KnowledgeStore
from ai_platform.messaging.bus import MessageBus
from ai_platform.secrets.manager import SecretsManager
from ai_platform.tool_host.sandbox import SandboxPolicy, SandboxViolation, ToolSandbox


@pytest.fixture
async def client(tmp_path: Path):
    settings = Settings(db_path=str(tmp_path / "test.db"), auth_required=False)
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac


def test_parse_agent_address():
    assert parse_agent_address("agents/foo") == ("agents/foo", None)
    assert parse_agent_address("support@acme.com") == ("support", "acme.com")


@pytest.mark.asyncio
async def test_local_embeddings_rag():
    store = KnowledgeStore(embedder=LocalHashEmbedding())
    spec = KnowledgeSourceSpec.model_validate(
        {
            "documents": [
                {"id": "d1", "text": "Refunds are available within 30 days of purchase."},
                {"id": "d2", "text": "Shipping takes 3-5 business days."},
            ],
            "ingestion": {"chunking": {"maxTokens": 64}},
            "retrieval": {"topK": 2},
        }
    )
    n = await store.ingest_source("policies", spec)
    assert n >= 2
    hits = await store.retrieve("refund policy", ["policies"], top_k=2)
    assert hits
    assert hits[0].score > 0
    assert store.embedding_backend == "local"


def test_build_embedding_defaults_local(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("PLATFORM_EMBEDDING_PROVIDER", "local")
    p = build_embedding_provider()
    assert p.name == "local"


@pytest.mark.asyncio
async def test_secrets_roundtrip(tmp_path: Path):
    sm = SecretsManager(str(tmp_path / "sec.db"), master_key="test-key")
    await sm.migrate()
    meta = await sm.put("ns1", "api-key", "sk-secret-123")
    assert meta.name == "api-key"
    assert await sm.get("ns1", "api-key") == "sk-secret-123"
    listed = await sm.list("ns1")
    assert len(listed) == 1
    token = sm.issue_lease("ns1", "api-key", ttl_seconds=60)
    assert await sm.resolve_lease(token) == "sk-secret-123"
    assert await sm.resolve_lease(token) is None  # one-time
    assert await sm.delete("ns1", "api-key")


@pytest.mark.asyncio
async def test_sandbox_blocks_metadata():
    sb = ToolSandbox(policy=SandboxPolicy(allowed_hosts=["api.example.com"]))
    with pytest.raises(SandboxViolation):
        sb.check_url("http://169.254.169.254/latest/meta-data/")
    with pytest.raises(SandboxViolation):
        sb.check_url("https://evil.com/x")
    sb.check_url("https://api.example.com/v1")


@pytest.mark.asyncio
async def test_federation_local_send(tmp_path: Path):
    bus = MessageBus(str(tmp_path / "bus.db"))
    await bus.migrate()
    gw = FederationGateway("local.test", bus)
    result = await gw.send_federated(
        "ns1",
        sender="agents/a",
        recipient="agents/b",
        payload={"ping": True},
    )
    assert result["mode"] == "local"
    assert result["message"]["recipient"] == "agents/b"


@pytest.mark.asyncio
async def test_federation_unknown_domain(tmp_path: Path):
    bus = MessageBus(str(tmp_path / "bus.db"))
    await bus.migrate()
    gw = FederationGateway("local.test", bus)
    with pytest.raises(ValueError, match="Unknown federated domain"):
        await gw.send_federated(
            "ns1",
            sender="agents/a",
            recipient="bot@remote.example",
            payload={},
        )


@pytest.mark.asyncio
async def test_api_secrets_and_federation(client: AsyncClient):
    ns = "default-org/default-project"
    health = (await client.get("/health")).json()
    assert health["version"] == "0.8.0"
    assert health["registryBackend"] == "sqlite"

    put = await client.put(f"/v1/{ns}/secrets/demo", json={"value": "s3cret"})
    assert put.status_code == 200
    listed = await client.get(f"/v1/{ns}/secrets")
    assert listed.status_code == 200
    assert any(s["name"] == "demo" for s in listed.json()["secrets"])

    info = await client.get("/v1/federation/info")
    assert info.status_code == 200
    assert "domain" in info.json()

    peer = await client.post(
        "/v1/federation/peers",
        json={"domain": "peer.test", "gateway": "http://127.0.0.1:9"},
    )
    assert peer.status_code == 200

    local = await client.post(
        f"/v1/{ns}/federation/send",
        json={
            "sender": "agents/console",
            "recipient": "agents/target",
            "payload": {"ok": True},
        },
    )
    assert local.status_code == 200
    assert local.json()["mode"] == "local"


@pytest.mark.asyncio
@pytest.mark.skipif(
    not (os.getenv("PLATFORM_TEST_DATABASE_URL") or os.getenv("PLATFORM_DATABASE_URL")),
    reason="no Postgres DSN",
)
async def test_postgres_registry_optional():
    from ai_platform.registry.postgres import PostgresRegistryStore
    from ai_platform.core.models import (
        PlatformResource,
        ResourceKind,
        ResourceMetadata,
    )

    dsn = os.environ.get("PLATFORM_TEST_DATABASE_URL") or os.environ["PLATFORM_DATABASE_URL"]
    store = PostgresRegistryStore(dsn)
    await store.migrate()
    ns = await store.ensure_namespace("acme/demo", "development")
    resource = PlatformResource(
        kind=ResourceKind.AGENT,
        metadata=ResourceMetadata(
            name="hello",
            namespace="acme/demo",
            version="1.0.0",
        ),
        spec={"role": "executor", "modelRef": "models/mock", "promptRef": "prompts/p"},
    )
    await store.upsert_resource_version(ns, resource)
    got = await store.get_resource(ns, ResourceKind.AGENT, "hello")
    assert got is not None
    await store.close()
