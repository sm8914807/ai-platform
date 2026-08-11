#!/usr/bin/env python3
"""Offline seed — writes demo data into PLATFORM_DB_PATH without HTTP.

Use when the API is stopped (avoids SQLite lock fights):

  pkill -f 'uvicorn ai_platform' || true
  PYTHONPATH=. .venv/bin/python scripts/seed_offline.py
  PYTHONPATH=. PLATFORM_DB_PATH=.platform/registry.db \\
    .venv/bin/python -m uvicorn ai_platform.api.app:create_app --factory --port 8080
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ai_platform.api.settings import Settings
from ai_platform.bundler.compiler import BundleCompiler
from ai_platform.core.models import (
    PlatformResource,
    ResourceKind,
    ResourceMetadata,
    ResourceStatus,
)
from ai_platform.db.sql import create_sql_backend, migrate_aux_stores
from ai_platform.federation.amtp import AMTPGateway, AMTPMessage, LocalAmtpAgent
from ai_platform.messaging.bus import MessageBus, RegisterInboxRequest, SendMessageRequest
from ai_platform.registry.sqlite import SqliteRegistryStore
from ai_platform.secrets.manager import SecretsManager
from ai_platform.context_graph.service import ContextGraphService, CreateTraceRequest
from ai_platform.discovery.service import AgentDiscoveryService, RegisterCapabilityRequest

NS = "default-org/default-project"
ENV = "development"
EXAMPLES = ROOT / "examples" / "resources"


def _strip_nulls(obj):
    if isinstance(obj, dict):
        return {k: _strip_nulls(v) for k, v in obj.items() if v is not None}
    if isinstance(obj, list):
        return [_strip_nulls(x) for x in obj]
    return obj


async def upsert_publish(registry: SqliteRegistryStore, ns_id: str, doc: dict) -> None:
    kind = ResourceKind(doc["kind"])
    name = doc["metadata"]["name"]
    version = str(doc["metadata"].get("version", "1.0.0"))
    resource = PlatformResource(
        kind=kind,
        metadata=ResourceMetadata(name=name, namespace=NS, version=version),
        spec=_strip_nulls(doc["spec"]),
        status=ResourceStatus(published=False),
    )
    await registry.upsert_resource_version(ns_id, resource, author_id="seed")
    await registry.publish(ns_id, kind, name, version)
    print(f"  ✓ {kind.value}/{name}@{version}")


async def main() -> None:
    db_path = os.environ.get("PLATFORM_DB_PATH", str(ROOT / ".platform" / "registry.db"))
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    print(f"Offline seed → {db_path}")

    sql = create_sql_backend(db_path=db_path)
    registry = SqliteRegistryStore(db_path)
    await registry.migrate()
    await migrate_aux_stores(sql)

    ns_id = await registry.ensure_namespace(NS, ENV)

    # Open policy first (registry.publish bypasses HTTP policy gates)
    await upsert_publish(
        registry,
        ns_id,
        {
            "kind": "Policy",
            "metadata": {"name": "seed-open", "version": "1.0.0"},
            "spec": {
                "rules": [
                    {
                        "effect": "allow",
                        "principals": ["*", "team:support", "anonymous"],
                        "actions": ["resource:publish", "agent:run"],
                        "resources": ["*"],
                    }
                ]
            },
        },
    )

    docs = []
    for path in sorted(EXAMPLES.glob("*.yaml")):
        for doc in yaml.safe_load_all(path.read_text()):
            if doc and "kind" in doc:
                docs.append(doc)
    docs.sort(key=lambda d: 1 if d["kind"] == "Policy" else 0)
    for doc in docs:
        try:
            await upsert_publish(registry, ns_id, doc)
        except Exception as e:
            print(f"  ! {doc['kind']}/{doc['metadata']['name']}: {e}")

    # extras
    extras = [
        {
            "kind": "Prompt",
            "metadata": {"name": "research", "version": "1.0.0"},
            "spec": {"template": "Research {{topic}}.", "variables": {"topic": {}}},
        },
        {
            "kind": "Agent",
            "metadata": {"name": "research-agent", "version": "1.0.0"},
            "spec": {
                "role": "executor",
                "modelRef": "models/gpt-4o-routed",
                "promptRef": "prompts/research",
            },
        },
        {
            "kind": "Tool",
            "metadata": {"name": "web-search", "version": "1.0.0"},
            "spec": {
                "adapter": "rest",
                "config": {"mock": True},
                "manifest": {
                    "name": "web-search",
                    "description": "Search",
                    "inputSchema": {"type": "object"},
                    "outputSchema": {"type": "object"},
                },
            },
        },
    ]
    for doc in extras:
        await upsert_publish(registry, ns_id, doc)

    graph = ContextGraphService(db_path=db_path, sql=sql)
    await graph.migrate()
    for tags, decision in (
        (["refund", "vip"], "approved_partial_refund"),
        (["discount", "renewal"], "granted_10pct"),
        (["escalation"], "routed_to_human"),
        (["refund", "fraud"], "denied_suspected_fraud"),
        (["onboarding"], "completed_kyc_check"),
    ):
        await graph.create_trace(
            ns_id,
            CreateTraceRequest(
                agent_ref="agents/support-agent",
                tags=tags,
                payload={"decision": decision},
                outcome="recorded",
            ),
        )
    print("  ✓ traces")

    discovery = AgentDiscoveryService(db_path=db_path, sql=sql)
    await discovery.migrate()
    for req in (
        RegisterCapabilityRequest(
            agent_ref="agents/support-agent",
            address="support@local.ai-platform",
            capabilities=["support", "refund", "billing"],
        ),
        RegisterCapabilityRequest(
            agent_ref="agents/research-agent",
            address="research@local.ai-platform",
            capabilities=["research", "summarize"],
        ),
        RegisterCapabilityRequest(
            agent_ref="agents/planner-agent",
            address="planner@local.ai-platform",
            capabilities=["plan", "orchestrate"],
        ),
    ):
        await discovery.register(ns_id, req)
    print("  ✓ discovery")

    bus = MessageBus(db_path=db_path, sql=sql)
    await bus.migrate()
    await bus.register_inbox(
        ns_id, RegisterInboxRequest(agent_address="agents/support-agent")
    )
    await bus.register_inbox(
        ns_id, RegisterInboxRequest(agent_address="agents/research-agent")
    )
    for recipient, payload in (
        ("agents/support-agent", {"ticket": "T-1001", "intent": "refund"}),
        ("agents/support-agent", {"ticket": "T-1002", "intent": "status"}),
        ("agents/research-agent", {"topic": "Q3 competitors"}),
    ):
        await bus.send(
            ns_id,
            SendMessageRequest(
                sender="agents/console", recipient=recipient, subject="seed", payload=payload
            ),
        )
    print("  ✓ messages")

    secrets = SecretsManager(db_path=db_path, sql=sql)
    await secrets.migrate()
    for name, value in (
        ("openai-key", "sk-demo-not-real"),
        ("stripe-webhook", "whsec_demo"),
        ("crm-token", "crm_demo_token"),
    ):
        await secrets.put(ns_id, name, value)
    print("  ✓ secrets")

    amtp = AMTPGateway("local.ai-platform", bus, sql=sql, namespace_id=ns_id)
    await amtp.register_agent(
        LocalAmtpAgent(address="support", api_key="agent-support-key", supported_schemas=["*"])
    )
    await amtp.register_agent(LocalAmtpAgent(address="research", supported_schemas=["*"]))
    await amtp.schemas.put(
        "agntcy:support.ticket.v1",
        {"type": "object", "properties": {"ticket": {"type": "string"}}},
    )
    amtp.register_peer("partner.example", "http://127.0.0.1:8081")
    await amtp.send(
        AMTPMessage(
            sender="console@local.ai-platform",
            recipients=["support@local.ai-platform"],
            payload={"ticket": "T-SEED", "intent": "hello"},
        ),
        namespace_id=ns_id,
    )
    print("  ✓ AMTP")

    published = await registry.list_published(ns_id)
    print(json.dumps({"published": len(published), "db": db_path}, indent=2))
    await sql.close()


if __name__ == "__main__":
    asyncio.run(main())
