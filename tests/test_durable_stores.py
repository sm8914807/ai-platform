"""Durable identity / memory / knowledge across process restarts (SqlBackend)."""

from __future__ import annotations

from pathlib import Path

import pytest

from ai_platform.auth.identity import IdentityStore
from ai_platform.core.models import KnowledgeSourceSpec, MemoryProfileSpec
from ai_platform.db.sql import create_sql_backend, migrate_aux_stores
from ai_platform.knowledge.service import KnowledgeService
from ai_platform.memory.service import MemoryService


@pytest.fixture
async def sql(tmp_path: Path):
    backend = create_sql_backend(db_path=str(tmp_path / "durable.db"))
    await migrate_aux_stores(backend)
    yield backend
    await backend.close()


@pytest.mark.asyncio
async def test_identity_survives_reopen(sql):
    store_a = IdentityStore(sql=sql)
    created = await store_a.create_user("default-org", "ops@example.com", "Ops")
    assert created.email == "ops@example.com"

    store_b = IdentityStore(sql=sql)
    found = await store_b.get_user_by_email("default-org", "ops@example.com")
    assert found is not None
    assert found.id == created.id
    assert found.display_name == "Ops"

    users = await store_b.list_users("default-org")
    assert any(u.email == "ops@example.com" for u in users)


@pytest.mark.asyncio
async def test_memory_survives_reopen(sql):
    profile = MemoryProfileSpec(
        layers=[{"type": "conversation", "backend": "memory"}],
        versioning=True,
    )
    scope = "session-durable-1"
    svc_a = MemoryService.durable(sql=sql)
    await svc_a.write(scope, {"role": "user", "content": "remember billing"}, profile)
    await svc_a.write(scope, {"role": "assistant", "content": "noted"}, profile)

    svc_b = MemoryService.durable(sql=sql)
    entries = await svc_b.read(scope, profile)
    assert len(entries) == 2
    assert entries[0].content["content"] == "remember billing"
    replay = await svc_b.replay(scope, from_version=1)
    assert len(replay) >= 2


@pytest.mark.asyncio
async def test_knowledge_rag_survives_reopen(sql):
    spec = KnowledgeSourceSpec(
        documents=[
            {
                "id": "doc1",
                "text": "Invoice billing support policy for enterprise customers.",
                "metadata": {"product": "billing"},
            },
            {
                "id": "doc2",
                "text": "Password reset instructions for all users.",
                "metadata": {"product": "auth"},
            },
        ],
        retrieval={"topK": 2},
    )
    svc_a = KnowledgeService.durable(sql=sql)
    await svc_a.ensure_source("kb-billing", spec)
    assert await svc_a.store.has_source("kb-billing")

    # New service instance — must not re-ingest, but must still retrieve.
    svc_b = KnowledgeService.durable(sql=sql)
    assert await svc_b.store.has_source("kb-billing")
    chunks = await svc_b.store.retrieve("invoice billing", ["kb-billing"], top_k=1)
    assert len(chunks) == 1
    assert "billing" in chunks[0].text.lower()
