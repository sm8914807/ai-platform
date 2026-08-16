"""Postgres-backed registry + aux stores + API (CI service job).

Runs only when PLATFORM_TEST_DATABASE_URL (or PLATFORM_DATABASE_URL) is set.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from ai_platform.api.app import create_app
from ai_platform.api.settings import Settings
from ai_platform.auth.identity import IdentityStore
from ai_platform.core.models import (
    KnowledgeSourceSpec,
    MemoryProfileSpec,
    PlatformResource,
    ResourceKind,
    ResourceMetadata,
)
from ai_platform.db.sql import create_sql_backend, migrate_aux_stores
from ai_platform.knowledge.service import KnowledgeService
from ai_platform.memory.service import MemoryService
from ai_platform.registry.postgres import PostgresRegistryStore

DSN = os.getenv("PLATFORM_TEST_DATABASE_URL") or os.getenv("PLATFORM_DATABASE_URL")

pytestmark = pytest.mark.skipif(not DSN, reason="no Postgres DSN (PLATFORM_TEST_DATABASE_URL)")


@pytest.fixture
def dsn() -> str:
    assert DSN
    return DSN


@pytest.mark.asyncio
async def test_postgres_registry_publish(dsn: str):
    store = PostgresRegistryStore(dsn)
    await store.migrate()
    suffix = uuid.uuid4().hex[:8]
    ns = await store.ensure_namespace(f"ci-org/{suffix}", "development")
    resource = PlatformResource(
        kind=ResourceKind.PROMPT,
        metadata=ResourceMetadata(
            name=f"p-{suffix}",
            namespace=f"ci-org/{suffix}",
            version="1.0.0",
        ),
        spec={"template": "hello {{ input }}"},
    )
    await store.upsert_resource_version(ns, resource)
    await store.publish(ns, ResourceKind.PROMPT, f"p-{suffix}", "1.0.0")
    published = await store.get_published_version(ns, ResourceKind.PROMPT, f"p-{suffix}")
    assert published is not None
    assert published.spec_json["template"].startswith("hello")
    listed = await store.list_published(ns)
    assert any(v.name == f"p-{suffix}" for v in listed)
    await store.close()


@pytest.mark.asyncio
async def test_postgres_aux_durable_stores(dsn: str, tmp_path: Path):
    sql = create_sql_backend(db_path=str(tmp_path / "unused.db"), database_url_override=dsn)
    await migrate_aux_stores(sql)

    identity = IdentityStore(sql=sql)
    email = f"ci-{uuid.uuid4().hex[:8]}@example.com"
    user = await identity.create_user("ci-org", email, "CI User")
    found = await identity.get_user_by_email("ci-org", email)
    assert found is not None
    assert found.id == user.id

    profile = MemoryProfileSpec(
        layers=[{"type": "conversation", "backend": "memory"}],
        versioning=True,
    )
    scope = f"pg-scope-{uuid.uuid4().hex[:8]}"
    memory = MemoryService.durable(sql=sql)
    await memory.write(scope, {"role": "user", "content": "postgres memory"}, profile)
    entries = await memory.read(scope, profile)
    assert len(entries) == 1

    knowledge = KnowledgeService.durable(sql=sql)
    source = f"kb-{uuid.uuid4().hex[:8]}"
    await knowledge.ensure_source(
        source,
        KnowledgeSourceSpec(
            documents=[{"id": "d1", "text": "Postgres RAG chunk about invoices."}],
            retrieval={"topK": 1},
        ),
    )
    chunks = await knowledge.retrieve("invoices", [f"knowledgesources/{source}"], {})
    assert chunks
    await sql.close()


@pytest.mark.asyncio
async def test_postgres_api_lifecycle(dsn: str, tmp_path: Path):
    settings = Settings(
        db_path=str(tmp_path / "aux-sqlite-for-git.db"),
        database_url=dsn,
        auth_required=True,
        auth_secret="ci-test-secret",
        cors_origins="http://localhost:5173",
    )
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            health = await ac.get("/health")
            assert health.status_code == 200

            denied = await ac.get("/v1/default-org/default-project/resources")
            assert denied.status_code == 401

            login = await ac.post(
                "/v1/auth/login",
                json={
                    "email": f"pg-api-{uuid.uuid4().hex[:6]}@example.com",
                    "orgId": "default-org",
                    "displayName": "PG CI",
                },
            )
            assert login.status_code == 200, login.text
            token = login.json()["accessToken"]
            headers = {"Authorization": f"Bearer {token}"}

            name = f"agent-{uuid.uuid4().hex[:6]}"
            upsert = await ac.put(
                f"/v1/default-org/default-project/Agent/{name}/versions/1.0.0",
                headers=headers,
                json={
                    "api_version": "platform.ai/v1",
                    "kind": "Agent",
                    "metadata": {
                        "name": name,
                        "namespace": "default-org/default-project",
                        "version": "1.0.0",
                    },
                    "spec": {
                        "role": "executor",
                        "modelRef": "models/mock",
                        "promptRef": "prompts/p",
                    },
                },
            )
            assert upsert.status_code == 200, upsert.text

            # Publish agent without eval suite — should succeed.
            pub = await ac.post(
                f"/v1/default-org/default-project/Agent/{name}/publish",
                headers=headers,
                json={"version": "1.0.0", "principal": "ci"},
            )
            assert pub.status_code == 200, pub.text
            assert pub.json().get("published") is True

            resources = await ac.get(
                "/v1/default-org/default-project/resources", headers=headers
            )
            assert resources.status_code == 200
            kinds = {r["kind"] for r in resources.json().get("resources", [])}
            assert "Agent" in kinds
