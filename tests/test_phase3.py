"""Phase 3 tests — multi-agent, marketplace, git sync, SSO/SCIM, terraform export."""

import pytest
from pathlib import Path

from ai_platform.agent.multi import MultiAgentEngine
from ai_platform.auth.identity import IdentityStore, ScimService
from ai_platform.auth.sso import OidcValidator, SsoService
from ai_platform.core.models import CollaborationSpec, PluginManifest, ScimUserPayload
from ai_platform.git_sync.service import GitSyncService
from ai_platform.marketplace.service import MarketplaceCatalog, MarketplaceService
from ai_platform.registry.memory import InMemoryRegistryStore
from ai_platform.terraform.export import export_terraform_json, write_terraform_files


def _agent_bundle():
    return {
        "Agent:planner-agent": {
            "kind": "Agent",
            "name": "planner-agent",
            "spec": {
                "role": "planner",
                "modelRef": "models/m",
                "promptRef": "prompts/p",
            },
        },
        "Agent:executor-agent": {
            "kind": "Agent",
            "name": "executor-agent",
            "spec": {
                "role": "executor",
                "modelRef": "models/m",
                "promptRef": "prompts/p",
            },
        },
        "Agent:reviewer-agent": {
            "kind": "Agent",
            "name": "reviewer-agent",
            "spec": {
                "role": "reviewer",
                "modelRef": "models/m",
                "promptRef": "prompts/p",
            },
        },
        "Prompt:p": {"kind": "Prompt", "name": "p", "spec": {"template": "Do: {{ message }}"}},
        "ModelRoute:m": {
            "kind": "ModelRoute",
            "name": "m",
            "spec": {
                "strategy": "weightedFallback",
                "candidates": [{"provider": "mock", "model": "mock-1", "weight": 100}],
            },
        },
    }


@pytest.mark.asyncio
async def test_multi_agent_planner_executor_reviewer():
    engine = MultiAgentEngine()
    collab = CollaborationSpec(
        pattern="planner_executor_reviewer",
        maxIterations=1,
        agents={
            "planner": "agents/planner-agent",
            "executor": "agents/executor-agent",
            "reviewer": "agents/reviewer-agent",
        },
    )
    result = await engine.run(
        _agent_bundle(),
        "agents/executor-agent",
        {"message": "billing task"},
        collaboration=collab,
    )
    assert result.pattern == "planner_executor_reviewer"
    assert len(result.steps) >= 3


@pytest.mark.asyncio
async def test_marketplace_install():
    store = InMemoryRegistryStore()
    import tempfile

    db = tempfile.mktemp(suffix=".db")
    catalog = MarketplaceCatalog(db)
    await catalog.migrate()
    svc = MarketplaceService(catalog, store)
    manifest = PluginManifest(
        type="prompt",
        version="1.0.0",
        tier="community",
        resources=[
            {
                "kind": "Prompt",
                "metadata": {"name": "plugin-prompt", "version": "1.0.0"},
                "spec": {"template": "from plugin"},
            }
        ],
    )
    await catalog.publish_plugin("test-plugin", manifest)
    ns = await store.ensure_namespace("org/project", "development")
    result = await svc.install(ns, "org/project", "test-plugin")
    assert "plugin-prompt" in str(result["resources"])


@pytest.mark.asyncio
async def test_git_sync_apply_export(tmp_path):
    store = InMemoryRegistryStore()
    import tempfile

    db = tempfile.mktemp(suffix=".db")
    git = GitSyncService(store, db)
    resources_dir = tmp_path / "resources"
    resources_dir.mkdir()
    (resources_dir / "prompt.yaml").write_text(
        "apiVersion: platform.ai/v1\nkind: Prompt\nmetadata:\n  name: git-p\n  version: '1.0.0'\n"
        "spec:\n  template: hello git\n"
    )
    ns = await store.ensure_namespace("org/project", "development")
    sync = await git.sync_from_directory(ns, "org/project", resources_dir)
    assert sync.applied == 1
    export_dir = tmp_path / "export"
    count = await git.export_to_directory(ns, "org/project", export_dir)
    assert count >= 1


@pytest.mark.asyncio
async def test_sso_and_scim():
    import tempfile

    db = tempfile.mktemp(suffix=".db")
    store = IdentityStore(db)
    # create tables manually for in-memory - use migrate via registry pattern
    import aiosqlite

    conn = await aiosqlite.connect(db)
    migration = Path(__file__).parent.parent / "migrations" / "003_phase3.sql"
    await conn.executescript(migration.read_text())
    await conn.commit()
    await conn.close()

    sso = SsoService(store, OidcValidator("test-secret"))
    login = await sso.login("default-org", "user@example.com", "User")
    assert "accessToken" in login
    ctx = sso.authenticate(f"Bearer {login['accessToken']}")
    assert ctx and ctx.email == "user@example.com"

    scim = ScimService(store)
    created = await scim.create_user(
        "default-org",
        ScimUserPayload(userName="scim@example.com", name={"formatted": "SCIM User"}),
    )
    assert created["userName"] == "scim@example.com"


def test_terraform_export(tmp_path):
    from ai_platform.registry.store import ResourceVersionRecord
    from datetime import datetime, timezone

    published = [
        ResourceVersionRecord(
            id="v1",
            resource_id="r1",
            version="1.0.0",
            spec_json={"template": "hi"},
            status_json={},
            author_id=None,
            commit_message=None,
            bundle_hash=None,
            created_at=datetime.now(timezone.utc),
            kind="Prompt",
            name="export-prompt",
        )
    ]
    out = tmp_path / "tf"
    count = write_terraform_files(published, "org/project", out)
    assert count == 1
    assert (out / "provider.tf").exists()
    json_out = export_terraform_json(published, "org/project")
    assert "export-prompt" in json_out
