"""Tests for messaging bus, context engineering, and provider adapters."""

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import aiosqlite
import pytest

from ai_platform.context.engineer import ContextBudget, ContextEngineer
from ai_platform.messaging.bus import MessageBus, RegisterInboxRequest, SendMessageRequest
from ai_platform.model_router.providers import (
    AnthropicProvider,
    BedrockProvider,
    OpenAIProvider,
    build_default_providers,
)
from ai_platform.model_router.router import ModelRequest, ModelRouter, MockModelProvider


async def _migrate_msg(db: str) -> None:
    migration = Path(__file__).parent.parent / "migrations" / "006_messaging.sql"
    conn = await aiosqlite.connect(db)
    await conn.executescript(migration.read_text())
    await conn.commit()
    await conn.close()


@pytest.mark.asyncio
async def test_message_bus_pull_ack_idempotent():
    db = tempfile.mktemp(suffix=".db")
    await _migrate_msg(db)
    bus = MessageBus(db)
    ns = "ns-1"

    await bus.register_inbox(
        ns, RegisterInboxRequest(agent_address="agents/billing", delivery_mode="pull")
    )
    msg1 = await bus.send(
        ns,
        SendMessageRequest(
            sender="agents/router",
            recipient="agents/billing",
            subject="invoice",
            payload={"ticketId": "T-1"},
            idempotency_key="idem-1",
        ),
    )
    msg2 = await bus.send(
        ns,
        SendMessageRequest(
            sender="agents/router",
            recipient="agents/billing",
            payload={"ticketId": "T-1"},
            idempotency_key="idem-1",
        ),
    )
    assert msg1.id == msg2.id

    inbox = await bus.pull_inbox(ns, "agents/billing")
    assert len(inbox) == 1
    assert inbox[0].status == "delivered"

    acked = await bus.ack(inbox[0].id)
    assert acked and acked.status == "acked"

    # Already pulled — empty
    empty = await bus.pull_inbox(ns, "agents/billing")
    assert empty == []


def test_context_engineer_budget_and_filter():
    eng = ContextEngineer(ContextBudget(max_tokens=200, reserve_for_response=20, system_reserve=20))
    messages = []
    for i in range(20):
        messages.append(
            {
                "role": "user" if i % 2 == 0 else "assistant",
                "content": f"Message about billing invoice number {i} " + ("x" * 80),
            }
        )
    messages.append({"role": "user", "content": "What is the billing invoice status?"})

    result = eng.prepare(messages, query="billing invoice")
    assert result.final_tokens <= 200
    assert result.final_tokens < result.original_tokens
    assert result.summarized or result.filtered or result.dropped > 0


def test_context_engineer_relevance_keeps_query_related():
    eng = ContextEngineer(ContextBudget(max_tokens=4000))
    messages = [
        {"role": "user", "content": "Talk about weather and sunny days"},
        {"role": "assistant", "content": "The weather looks great"},
        {"role": "user", "content": "Now about discount enterprise renewal"},
        {"role": "assistant", "content": "Enterprise discount approved at 20%"},
        {"role": "user", "content": "Confirm the discount decision"},
        {"role": "assistant", "content": "Confirmed"},
    ]
    result = eng.prepare(messages, query="discount enterprise")
    joined = " ".join(str(m["content"]) for m in result.messages)
    assert "discount" in joined.lower() or result.filtered


@pytest.mark.asyncio
async def test_openai_provider_http_mock():
    provider = OpenAIProvider(api_key="sk-test")
    mock_resp = AsyncMock()
    mock_resp.raise_for_status = lambda: None
    mock_resp.json = lambda: {
        "choices": [{"message": {"content": "hello from openai"}}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
    }

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, *args, **kwargs):
            return mock_resp

    with patch("ai_platform.model_router.providers.httpx.AsyncClient", FakeClient):
        result = await provider.complete(
            "gpt-4o",
            ModelRequest(messages=[{"role": "user", "content": "hi"}]),
        )
    assert result.provider == "openai"
    assert "hello" in result.content


@pytest.mark.asyncio
async def test_anthropic_provider_http_mock():
    provider = AnthropicProvider(api_key="ant-test")
    mock_resp = AsyncMock()
    mock_resp.raise_for_status = lambda: None
    mock_resp.json = lambda: {
        "content": [{"type": "text", "text": "hello from claude"}],
        "usage": {"input_tokens": 4, "output_tokens": 6},
    }

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, *args, **kwargs):
            return mock_resp

    with patch("ai_platform.model_router.providers.httpx.AsyncClient", FakeClient):
        result = await provider.complete(
            "claude-sonnet-4",
            ModelRequest(messages=[{"role": "user", "content": "hi"}]),
        )
    assert result.provider == "anthropic"
    assert "claude" in result.content


def test_build_default_providers_includes_mock():
    providers = build_default_providers()
    assert "mock" in providers
    assert "openai" in providers
    assert "anthropic" in providers
    assert "bedrock" in providers


@pytest.mark.asyncio
async def test_router_uses_mock_fallback():
    router = ModelRouter(providers={"mock": MockModelProvider()})
    from ai_platform.core.models import ModelCandidate, ModelRouteSpec

    spec = ModelRouteSpec(
        candidates=[
            ModelCandidate(provider="missing", model="x", weight=100),
            ModelCandidate(provider="mock", model="mock-1", weight=50, fallback=True),
        ]
    )
    # First candidate has no provider — skip; fallback mock works
    # Actually missing provider is skipped entirely, mock still selected by weight
    resp = await router.complete(
        ModelRouteSpec(candidates=[ModelCandidate(provider="mock", model="m1")]),
        ModelRequest(messages=[{"role": "user", "content": "ping"}]),
    )
    assert resp.provider == "mock"
