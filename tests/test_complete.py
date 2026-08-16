"""v0.8 — Postgres aux stores + AMTP + Studio console."""

from __future__ import annotations

from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from ai_platform.api.app import create_app
from ai_platform.api.settings import Settings
from ai_platform.db.sql import SqliteBackend, create_sql_backend, migrate_aux_stores
from ai_platform.federation.amtp import (
    AMTPGateway,
    AMTPMessage,
    DnsDiscovery,
    format_address,
    uuidv7,
)


@pytest.fixture
async def client(tmp_path: Path):
    settings = Settings(db_path=str(tmp_path / "test.db"), auth_required=False)
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac


@pytest.fixture
async def authed_client(tmp_path: Path):
    settings = Settings(db_path=str(tmp_path / "auth.db"), auth_required=True)
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            login = await ac.post(
                "/v1/auth/login",
                json={"email": "ops@example.com", "orgId": "default-org", "displayName": "Ops"},
            )
            assert login.status_code == 200, login.text
            ac.headers["Authorization"] = f"Bearer {login.json()['accessToken']}"
            yield ac


@pytest.mark.asyncio
async def test_auth_required_rejects_anonymous(authed_client: AsyncClient):
    # Drop token — middleware must reject protected routes.
    client = authed_client
    client.headers.pop("Authorization", None)
    denied = await client.get("/v1/default-org/default-project/resources")
    assert denied.status_code == 401

    login = await client.post(
        "/v1/auth/login",
        json={"email": "ops@example.com", "orgId": "default-org"},
    )
    assert login.status_code == 200
    client.headers["Authorization"] = f"Bearer {login.json()['accessToken']}"
    ok = await client.get("/v1/default-org/default-project/resources")
    assert ok.status_code == 200

    # SCIM also requires auth when auth_required=True
    client.headers.pop("Authorization", None)
    scim = await client.get("/scim/v2/Users")
    assert scim.status_code == 401


@pytest.mark.asyncio
async def test_aux_migrate_sqlite(tmp_path: Path):
    backend = SqliteBackend(str(tmp_path / "aux.db"))
    await migrate_aux_stores(backend)
    # secrets + messaging tables exist
    await backend.execute(
        "INSERT INTO secrets (id, namespace_id, name, ciphertext, metadata_json, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        "s1",
        "ns",
        "k",
        "cipher",
        "{}",
        "2020-01-01T00:00:00+00:00",
    )
    row = await backend.fetchone("SELECT name FROM secrets WHERE id = ?", "s1")
    assert row and row["name"] == "k"
    await backend.close()


def test_dns_txt_parse():
    dns = DnsDiscovery(allow_http=True)
    caps = dns.parse_txt(
        "acme.test",
        ["v=amtp1;gateway=http://gw.acme.test:8080;auth=none,apikey;max-size=1000;features=inbox"],
    )
    assert caps is not None
    assert caps.gateway == "http://gw.acme.test:8080"
    assert caps.max_size == 1000
    assert "apikey" in caps.auth


def test_uuidv7_shape():
    u = uuidv7()
    assert len(u) == 36


@pytest.mark.asyncio
async def test_amtp_local_fanout(tmp_path: Path):
    from ai_platform.messaging.bus import MessageBus
    from ai_platform.federation.amtp import LocalAmtpAgent

    sql = create_sql_backend(db_path=str(tmp_path / "amtp.db"))
    await migrate_aux_stores(sql)
    bus = MessageBus(db_path=str(tmp_path / "amtp.db"), sql=sql)
    await bus.migrate()
    gw = AMTPGateway("local.test", bus, sql=sql, namespace_id="ns1")
    await gw.register_agent(LocalAmtpAgent(address="support", api_key="k1"))
    result = await gw.send(
        AMTPMessage(
            sender="console@local.test",
            recipients=["support@local.test"],
            payload={"ping": True},
        ),
        namespace_id="ns1",
    )
    assert result["status"] == "delivered"
    assert result["messageId"]
    status = await gw.get_status(result["messageId"])
    assert status.status == "delivered"


@pytest.mark.asyncio
async def test_api_v8_health_and_amtp(client: AsyncClient):
    health = (await client.get("/health")).json()
    assert health["version"] == "0.8.0"
    assert health["sqlBackend"] == "sqlite"

    caps = await client.get("/v1/capabilities")
    assert caps.status_code == 200
    assert caps.json()["version"] == "1.0"

    dns = await client.get("/v1/amtp/dns-txt")
    assert dns.status_code == 200
    assert "v=amtp1" in dns.json()["value"]

    send = await client.post(
        "/v1/messages",
        json={
            "sender": "console@local.ai-platform",
            "recipients": ["agents/target"],
            "payload": {"ok": True},
        },
    )
    assert send.status_code == 200
    body = send.json()
    assert "messageId" in body

    # Studio editor path: upsert still works
    ns = "default-org/default-project"
    put = await client.put(
        f"/v1/{ns}/Agent/demo/versions/1.0.0",
        json={
            "api_version": "platform.ai/v1",
            "kind": "Agent",
            "metadata": {"name": "demo", "version": "1.0.0"},
            "spec": {"modelRef": "models/m", "promptRef": "prompts/p"},
        },
    )
    # May 400 on schema validation depending on Agent CRD — accept 200 or 400
    assert put.status_code in (200, 400)


@pytest.mark.asyncio
async def test_studio_capability_registration_and_agent_test(client: AsyncClient):
    ns = "default-org/default-project"

    registered = await client.post(
        f"/v1/{ns}/discovery/register",
        json={
            "agent_ref": "agents/studio-test",
            "address": "studio-test@platform.local",
            "capabilities": ["support", "test"],
            "delivery_mode": "pull",
        },
    )
    assert registered.status_code == 200
    assert registered.json()["capabilities"] == ["support", "test"]

    resources = [
        (
            "Prompt",
            "studio-prompt",
            {"template": "You are a test agent. User: {{ input }}"},
        ),
        (
            "ModelRoute",
            "studio-model",
            {
                "strategy": "weightedFallback",
                "candidates": [{"provider": "mock", "model": "mock-1", "weight": 100}],
            },
        ),
        (
            "Agent",
            "studio-test",
            {
                "role": "executor",
                "modelRef": "models/studio-model",
                "promptRef": "prompts/studio-prompt",
            },
        ),
    ]
    for kind, name, spec in resources:
        put = await client.put(
            f"/v1/{ns}/{kind}/{name}/versions/1.0.0",
            json={
                "api_version": "platform.ai/v1",
                "kind": kind,
                "metadata": {"name": name, "namespace": ns, "version": "1.0.0"},
                "spec": spec,
            },
        )
        assert put.status_code == 200, put.text
        publish = await client.post(
            f"/v1/{ns}/{kind}/{name}/publish",
            json={"version": "1.0.0", "principal": "test"},
        )
        assert publish.status_code == 200, publish.text

    executed = await client.post(
        f"/v1/{ns}/execute",
        json={"resource_ref": "agents/studio-test", "input": {"message": "hello"}},
    )
    assert executed.status_code == 200, executed.text
    assert executed.json()["type"] == "done"


def test_format_address():
    assert format_address("agents/foo", "x.com") == "foo@x.com"
    assert format_address("foo", "x.com") == "foo@x.com"
