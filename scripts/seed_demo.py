#!/usr/bin/env python3
"""Seed a rich demo namespace so Platform Studio isn't empty."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import httpx
import yaml

API = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8080"
NS = "default-org/default-project"
ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples" / "resources"
ADMIN = {"X-Admin-Key": "dev-admin-key"}


async def put_resource(client: httpx.AsyncClient, doc: dict) -> None:
    kind = doc["kind"]
    name = doc["metadata"]["name"]
    version = doc["metadata"].get("version", "1.0.0")
    body = {
        "api_version": doc.get("apiVersion", "platform.ai/v1"),
        "kind": kind,
        "metadata": {
            "name": name,
            "version": version,
            "namespace": NS,
            "labels": doc["metadata"].get("labels", {}),
        },
        "spec": doc["spec"],
    }
    r = await client.put(f"/v1/{NS}/{kind}/{name}/versions/{version}", json=body)
    if r.status_code >= 400:
        print(f"  ! upsert {kind}/{name}: {r.status_code} {r.text[:200]}")
        return
    p = await client.post(
        f"/v1/{NS}/{kind}/{name}/publish",
        json={"version": version, "principal": "team:support"},
    )
    if p.status_code >= 400:
        print(f"  ! publish {kind}/{name}: {p.status_code} {p.text[:200]}")
    else:
        print(f"  ✓ {kind}/{name}@{version}")


async def run() -> None:
    print(f"Seeding {API} namespace={NS}")
    async with httpx.AsyncClient(base_url=API, timeout=30.0) as client:
        health = (await client.get("/health")).json()
        print(f"backend={health.get('sqlBackend')} version={health.get('version')}")

        # Open publish gate for seeding (team:support can publish anything)
        await put_resource(
            client,
            {
                "apiVersion": "platform.ai/v1",
                "kind": "Policy",
                "metadata": {"name": "seed-open", "version": "1.0.0"},
                "spec": {
                    "rules": [
                        {
                            "effect": "allow",
                            "principals": ["team:support", "anonymous", "*"],
                            "actions": ["resource:publish", "agent:run"],
                            "resources": ["*"],
                        }
                    ]
                },
            },
        )

        # --- CRDs from examples (Policy last) ---
        yaml_docs: list[dict] = []
        for path in sorted(EXAMPLES.glob("*.yaml")):
            for doc in yaml.safe_load_all(path.read_text()):
                if doc and "kind" in doc:
                    yaml_docs.append(doc)
        yaml_docs.sort(key=lambda d: 1 if d["kind"] == "Policy" else 0)
        for doc in yaml_docs:
            await put_resource(client, doc)

        # Extra demo agents / tools
        extras = [
            {
                "apiVersion": "platform.ai/v1",
                "kind": "Prompt",
                "metadata": {"name": "research", "version": "1.0.0"},
                "spec": {
                    "template": "Research {{topic}} and cite sources.",
                    "variables": {"topic": "string"},
                },
            },
            {
                "apiVersion": "platform.ai/v1",
                "kind": "Agent",
                "metadata": {"name": "research-agent", "version": "1.0.0"},
                "spec": {
                    "role": "executor",
                    "modelRef": "models/gpt-4o-routed",
                    "promptRef": "prompts/research",
                },
            },
            {
                "apiVersion": "platform.ai/v1",
                "kind": "Tool",
                "metadata": {"name": "web-search", "version": "1.0.0"},
                "spec": {
                    "adapter": "rest",
                    "config": {"url": "https://httpbin.org/post", "method": "POST", "mock": True},
                    "manifest": {
                        "name": "web-search",
                        "description": "Search the web",
                        "inputSchema": {"type": "object"},
                        "outputSchema": {"type": "object"},
                    },
                },
            },
        ]
        for doc in extras:
            await put_resource(client, doc)

        # --- Discovery sync ---
        sync = await client.post(f"/v1/{NS}/discovery/sync")
        print(f"discovery sync: {sync.status_code} {sync.text[:120]}")

        # Explicit discovery regs
        for agent in (
            {
                "agent_ref": "agents/support-agent",
                "address": "support@local.ai-platform",
                "capabilities": ["support", "refund", "billing"],
                "schemas": ["agntcy:support.*"],
            },
            {
                "agent_ref": "agents/research-agent",
                "address": "research@local.ai-platform",
                "capabilities": ["research", "summarize"],
                "schemas": ["agntcy:research.*"],
            },
            {
                "agent_ref": "agents/planner",
                "address": "planner@local.ai-platform",
                "capabilities": ["plan", "orchestrate"],
            },
        ):
            await client.post(f"/v1/{NS}/discovery/register", json=agent)

        # --- Traces ---
        for tags, decision in (
            (["refund", "vip"], "approved_partial_refund"),
            (["discount", "renewal"], "granted_10pct"),
            (["escalation"], "routed_to_human"),
            (["refund", "fraud"], "denied_suspected_fraud"),
            (["onboarding"], "completed_kyc_check"),
        ):
            await client.post(
                f"/v1/{NS}/traces",
                json={
                    "agent_ref": "agents/support-agent",
                    "tags": tags,
                    "payload": {"decision": decision, "confidence": 0.86},
                    "outcome": "recorded",
                },
            )
        print("  ✓ decision traces")

        # --- Inbox + messages ---
        await client.post(
            f"/v1/{NS}/inbox/register",
            json={"agent_address": "agents/support-agent", "delivery_mode": "pull"},
        )
        await client.post(
            f"/v1/{NS}/inbox/register",
            json={"agent_address": "agents/research-agent", "delivery_mode": "pull"},
        )
        for payload in (
            {"ticket": "T-1001", "intent": "refund"},
            {"ticket": "T-1002", "intent": "status"},
            {"topic": "Q3 competitors", "depth": "brief"},
        ):
            await client.post(
                f"/v1/{NS}/messages",
                json={
                    "sender": "agents/console",
                    "recipient": "agents/support-agent"
                    if "ticket" in payload
                    else "agents/research-agent",
                    "subject": "seed",
                    "payload": payload,
                },
            )
        print("  ✓ messages")

        # --- Secrets (placeholder values) ---
        for name, value in (
            ("openai-key", "sk-demo-not-real"),
            ("stripe-webhook", "whsec_demo"),
            ("crm-token", "crm_demo_token"),
        ):
            await client.put(f"/v1/{NS}/secrets/{name}", json={"value": value})
        print("  ✓ secrets")

        # --- Federation / AMTP ---
        await client.post(
            "/v1/federation/peers",
            json={"domain": "partner.example", "gateway": "http://127.0.0.1:8081"},
        )
        await client.post(
            "/v1/amtp/peers",
            json={"domain": "partner.example", "gateway": "http://127.0.0.1:8081"},
        )
        await client.post(
            "/v1/admin/agents",
            headers=ADMIN,
            json={
                "address": "support",
                "api_key": "agent-support-key",
                "delivery_mode": "pull",
                "supported_schemas": ["agntcy:support.*", "*"],
            },
        )
        await client.post(
            "/v1/admin/agents",
            headers=ADMIN,
            json={
                "address": "research",
                "delivery_mode": "pull",
                "supported_schemas": ["agntcy:research.*"],
            },
        )
        await client.post(
            "/v1/admin/schemas",
            headers=ADMIN,
            json={
                "schemaId": "agntcy:support.ticket.v1",
                "version": "1.0",
                "definition": {
                    "type": "object",
                    "properties": {"ticket": {"type": "string"}, "intent": {"type": "string"}},
                },
            },
        )
        await client.post(
            "/v1/messages",
            json={
                "sender": "console@local.ai-platform",
                "recipients": ["support@local.ai-platform"],
                "subject": "seed-ping",
                "schema": "agntcy:support.ticket.v1",
                "payload": {"ticket": "T-SEED", "intent": "hello"},
            },
        )
        print("  ✓ AMTP agents/schemas/peers")

        # --- Summary ---
        resources = (await client.get(f"/v1/{NS}/resources")).json()["resources"]
        traces = (await client.get(f"/v1/{NS}/traces")).json()["traces"]
        agents = (await client.get(f"/v1/{NS}/discovery/agents")).json()["agents"]
        secrets = (await client.get(f"/v1/{NS}/secrets")).json()["secrets"]
        messages = (await client.get(f"/v1/{NS}/messages")).json()["messages"]
        peers = (await client.get("/v1/federation/peers")).json()["peers"]
        print(
            json.dumps(
                {
                    "resources": len(resources),
                    "traces": len(traces),
                    "agents": len(agents),
                    "secrets": len(secrets),
                    "messages": len(messages),
                    "peers": len(peers),
                },
                indent=2,
            )
        )


if __name__ == "__main__":
    asyncio.run(run())
