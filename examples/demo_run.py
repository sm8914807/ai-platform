"""Seed example resources and run agent locally."""

import asyncio
import json
from pathlib import Path

import yaml

from ai_platform.api.app import create_app
from ai_platform.api.settings import Settings
from ai_platform.core.models import PlatformResource, ResourceKind, ResourceMetadata
from ai_platform.registry.sqlite import SqliteRegistryStore
from ai_platform.sdk.platform import Platform

EXAMPLES = Path(__file__).parent / "resources"


async def seed() -> None:
    settings = Settings(db_path=".platform/demo.db")
    store = SqliteRegistryStore(settings.db_path)
    await store.migrate()
    ns_id = await store.ensure_namespace(settings.default_namespace, settings.default_env)

    for path in sorted(EXAMPLES.glob("*.yaml")):
        doc = yaml.safe_load(path.read_text())
        resource = PlatformResource(
            kind=ResourceKind(doc["kind"]),
            metadata=ResourceMetadata(
                name=doc["metadata"]["name"],
                namespace=doc["metadata"]["namespace"],
                version=doc["metadata"].get("version", "1.0.0"),
            ),
            spec=doc["spec"],
        )
        await store.upsert_resource_version(ns_id, resource)
        await store.publish(
            ns_id,
            resource.kind,
            resource.metadata.name,
            resource.metadata.version,
        )
        print(f"published {doc['kind']}/{doc['metadata']['name']}")


async def main() -> None:
    await seed()
    platform = await Platform.start(
        endpoint="http://localhost:8080",
        namespace="default-org/default-project",
        environment="development",
    )
    result = await platform.run(
        "agents/support-agent",
        input={"message": "I need help with invoice #42"},
        stream=True,
    )
    async for event in result.stream:
        print(json.dumps({"type": event.type, "data": event.data}))


if __name__ == "__main__":
    asyncio.run(main())
