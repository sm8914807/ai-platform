"""FastAPI application factory."""

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

from ai_platform.api.settings import Settings
from ai_platform.api.state import AppState
from ai_platform.bundler.compiler import BundleCompiler
from ai_platform.core.ids import new_id
from ai_platform.core.models import (
    PlatformResource,
    ResourceKind,
    ResourceMetadata,
    ResourceStatus,
)
from ai_platform.core.validation import validate_platform_resource
from ai_platform.registry.sqlite import SqliteRegistryStore


class ResourceUpsertBody(BaseModel):
    api_version: str = "platform.ai/v1"
    kind: str
    metadata: dict[str, Any]
    spec: dict[str, Any]
    status: dict[str, Any] | None = None


class PublishBody(BaseModel):
    version: str
    principal: str = "anonymous"
    eval_suite_ref: str | None = Field(default=None, alias="evalSuiteRef")

    model_config = {"populate_by_name": True}


class RegisterNodeBody(BaseModel):
    namespace: str
    environment: str = "development"
    node_type: str = "sdk"
    metadata: dict[str, Any] = Field(default_factory=dict)


class RegisterEdgeBody(BaseModel):
    namespace: str
    environment: str = "development"
    region: str | None = None
    node_type: str = "edge"
    bundle_cache_path: str | None = Field(default=None, alias="bundleCachePath")
    metadata: dict[str, Any] = Field(default_factory=dict)

    model_config = {"populate_by_name": True}


class RegisterRegionBody(BaseModel):
    name: str
    endpoint: str
    data_residency: str | None = Field(default=None, alias="dataResidency")
    is_primary: bool = Field(default=False, alias="isPrimary")

    model_config = {"populate_by_name": True}


class InstallComplianceBody(BaseModel):
    pack_id: str = Field(alias="packId")
    installed_by: str | None = Field(default=None, alias="installedBy")

    model_config = {"populate_by_name": True}


class TelemetryBody(BaseModel):
    events: list[dict[str, Any]] = Field(default_factory=list)


class PromoteBody(BaseModel):
    from_env: str = Field(alias="fromEnv")
    to_env: str = Field(alias="toEnv")
    requested_by: str = Field(default="admin", alias="requestedBy")

    model_config = {"populate_by_name": True}


class ApprovePromotionBody(BaseModel):
    approved_by: str = Field(default="admin", alias="approvedBy")

    model_config = {"populate_by_name": True}


class WorkflowApproveBody(BaseModel):
    decision: str = "approved"


class LoginBody(BaseModel):
    email: str
    org_id: str = Field(default="default-org", alias="orgId")
    display_name: str | None = Field(default=None, alias="displayName")

    model_config = {"populate_by_name": True}


class PublishPluginBody(BaseModel):
    name: str
    manifest: dict[str, Any]


class InstallPluginBody(BaseModel):
    plugin_name: str = Field(alias="pluginName")
    version: str | None = None
    installed_by: str | None = Field(default=None, alias="installedBy")

    model_config = {"populate_by_name": True}


class GitSyncBody(BaseModel):
    directory: str
    publish: bool = True
    author: str | None = None


class ScimUserBody(BaseModel):
    schemas: list[str] = Field(default_factory=lambda: ["urn:ietf:params:scim:schemas:core:2.0:User"])
    userName: str
    name: dict[str, str] = Field(default_factory=dict)
    emails: list[dict[str, str]] = Field(default_factory=list)
    active: bool = True
    externalId: str | None = None


def _auth_principal(request: Request, st: AppState) -> str:
    auth = request.headers.get("Authorization")
    ctx = st.sso_service.authenticate(auth)
    return ctx.principal if ctx else "anonymous"


def _parse_resource(body: ResourceUpsertBody, namespace: str) -> PlatformResource:
    meta = body.metadata
    status = body.status or {}
    return PlatformResource(
        kind=ResourceKind(body.kind),
        metadata=ResourceMetadata(
            name=meta["name"],
            namespace=namespace,
            version=meta.get("version", "0.0.1"),
            labels=meta.get("labels", {}),
            annotations=meta.get("annotations", {}),
        ),
        spec=body.spec,
        status=ResourceStatus(
            published=status.get("published", False),
            bundle_hash=status.get("bundleHash"),
        ),
    )


def _bundle_index(published: list) -> dict[str, dict]:
    index: dict[str, dict] = {}
    for v in published:
        if v.kind and v.name:
            index[f"{v.kind}:{v.name}"] = {
                "kind": v.kind,
                "name": v.name,
                "spec": v.spec_json,
            }
    return index


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    import os

    from ai_platform.db.backend import database_url, is_postgres

    dsn = settings.database_url or database_url() or os.getenv("DATABASE_URL")
    backend = "postgres" if is_postgres(dsn) else "sqlite"
    if backend == "postgres":
        from ai_platform.registry.postgres import PostgresRegistryStore

        registry = PostgresRegistryStore(dsn)  # type: ignore[arg-type]
    else:
        registry = SqliteRegistryStore(settings.db_path)
    bundler = BundleCompiler()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        from ai_platform.db.sql import create_sql_backend, migrate_aux_stores

        sql = create_sql_backend(db_path=settings.db_path, database_url_override=dsn)
        await registry.migrate()
        await migrate_aux_stores(sql)
        app.state.platform = AppState(
            registry,
            bundler,
            settings.db_path,
            federation_domain=settings.federation_domain,
            secrets_key=settings.secrets_key,
            sandbox_timeout_seconds=settings.sandbox_timeout_seconds,
            backend=backend,
            sql=sql,
            database_url=dsn,
        )
        # Per-service migrate remains for sqlite unit paths / idempotent DDL
        await app.state.platform.workflow_engine.initialize()
        await app.state.platform.region_service.migrate()
        await app.state.platform.context_graph.migrate()
        await app.state.platform.discovery.migrate()
        await app.state.platform.dynamic_workflows.migrate()
        await app.state.platform.message_bus.migrate()
        await app.state.platform.secrets.migrate()
        yield
        await sql.close()
        if backend == "postgres" and hasattr(registry, "close"):
            await registry.close()

    app = FastAPI(title="AI Platform Control Plane", version="0.8.0", lifespan=lifespan)

    from fastapi.middleware.cors import CORSMiddleware

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    def state(request: Request) -> AppState:
        return request.app.state.platform

    @app.get("/health")
    async def health(request: Request):
        st = getattr(request.app.state, "platform", None)
        return {
            "status": "ok",
            "version": "0.8.0",
            "publicKey": bundler.public_key_hex,
            "registryBackend": st.backend if st else backend,
            "sqlBackend": st.sql.kind if st else backend,
            "federationDomain": settings.federation_domain,
        }

    # --- Message Bus ---
    @app.post("/v1/{namespace:path}/inbox/register")
    async def register_inbox(
        namespace: str, body: dict, request: Request, environment: str | None = None
    ):
        st = state(request)
        from ai_platform.messaging.bus import RegisterInboxRequest

        env = environment or settings.default_env
        ns_id = await st.registry.ensure_namespace(namespace, env)
        req = RegisterInboxRequest.model_validate(body)
        return await st.message_bus.register_inbox(ns_id, req)

    @app.post("/v1/{namespace:path}/messages")
    async def send_message(
        namespace: str, body: dict, request: Request, environment: str | None = None
    ):
        st = state(request)
        from ai_platform.messaging.bus import SendMessageRequest

        env = environment or settings.default_env
        ns_id = await st.registry.ensure_namespace(namespace, env)
        req = SendMessageRequest.model_validate(body)
        msg = await st.message_bus.send(ns_id, req)
        return msg.model_dump(mode="json")

    @app.get("/v1/{namespace:path}/inbox/{agent_address:path}")
    async def pull_inbox(
        namespace: str,
        agent_address: str,
        request: Request,
        environment: str | None = None,
        limit: int = 20,
    ):
        st = state(request)
        env = environment or settings.default_env
        ns_id = await st.registry.ensure_namespace(namespace, env)
        auth = request.headers.get("Authorization", "")
        bearer = auth.removeprefix("Bearer ").strip() if auth.startswith("Bearer ") else None
        local = agent_address.removeprefix("agents/")
        full = local if "@" in local else f"{local}@{st.amtp.domain}"
        if full in st.amtp._agents and st.amtp._agents[full].api_key:
            if not st.amtp.verify_agent_key(full, bearer):
                raise HTTPException(401, "invalid agent api key")
        addr = agent_address if agent_address.startswith("agents/") else f"agents/{agent_address}"
        messages = await st.message_bus.pull_inbox(ns_id, addr, limit)
        return {"messages": [m.model_dump(mode="json") for m in messages]}

    @app.post("/v1/messages/{message_id}/ack")
    async def ack_message(message_id: str, request: Request):
        st = state(request)
        msg = await st.message_bus.ack(message_id)
        if not msg:
            raise HTTPException(404, detail="Message not found")
        return msg.model_dump(mode="json")

    @app.get("/v1/{namespace:path}/messages")
    async def list_messages(
        namespace: str,
        request: Request,
        agent: str | None = None,
        environment: str | None = None,
        limit: int = 50,
    ):
        st = state(request)
        env = environment or settings.default_env
        ns_id = await st.registry.ensure_namespace(namespace, env)
        messages = await st.message_bus.list_messages(ns_id, agent, limit)
        return {"messages": [m.model_dump(mode="json") for m in messages]}

    # --- Secrets ---
    @app.put("/v1/{namespace:path}/secrets/{name}")
    async def put_secret(
        namespace: str,
        name: str,
        body: dict,
        request: Request,
        environment: str | None = None,
    ):
        st = state(request)
        env = environment or settings.default_env
        ns_id = await st.registry.ensure_namespace(namespace, env)
        value = body.get("value")
        if not isinstance(value, str) or not value:
            raise HTTPException(400, "value is required")
        meta = await st.secrets.put(ns_id, name, value, body.get("metadata"))
        return meta.model_dump(mode="json")

    @app.get("/v1/{namespace:path}/secrets")
    async def list_secrets(
        namespace: str, request: Request, environment: str | None = None
    ):
        st = state(request)
        env = environment or settings.default_env
        ns_id = await st.registry.ensure_namespace(namespace, env)
        items = await st.secrets.list(ns_id)
        return {"secrets": [s.model_dump(mode="json") for s in items]}

    @app.delete("/v1/{namespace:path}/secrets/{name}")
    async def delete_secret(
        namespace: str, name: str, request: Request, environment: str | None = None
    ):
        st = state(request)
        env = environment or settings.default_env
        ns_id = await st.registry.ensure_namespace(namespace, env)
        ok = await st.secrets.delete(ns_id, name)
        if not ok:
            raise HTTPException(404, "secret not found")
        return {"deleted": True}

    @app.post("/v1/{namespace:path}/secrets/{name}/lease")
    async def lease_secret(
        namespace: str,
        name: str,
        request: Request,
        environment: str | None = None,
        ttl: int = 300,
    ):
        st = state(request)
        env = environment or settings.default_env
        ns_id = await st.registry.ensure_namespace(namespace, env)
        token = st.secrets.issue_lease(ns_id, name, ttl_seconds=ttl)
        return {"leaseToken": token, "ttlSeconds": ttl}

    # --- AMTP Federation ---
    @app.get("/v1/federation/info")
    @app.get("/.well-known/amtp")
    async def federation_info(request: Request):
        st = state(request)
        return st.federation.info()

    @app.post("/v1/federation/peers")
    async def register_peer(body: dict, request: Request):
        st = state(request)
        from ai_platform.federation.gateway import FederatedDomain

        if body.get("gateway") and not body.get("domain"):
            domain = await st.federation.registry.discover_http(body["gateway"])
            return domain.model_dump(mode="json")
        domain = FederatedDomain.model_validate(body)
        st.federation.registry.register(domain)
        if body.get("apiKey"):
            st.federation.api_keys[domain.domain] = body["apiKey"]
        return domain.model_dump(mode="json")

    @app.get("/v1/federation/peers")
    async def list_peers(request: Request):
        st = state(request)
        return {"peers": [p.model_dump(mode="json") for p in st.federation.registry.list()]}

    @app.post("/v1/{namespace:path}/federation/send")
    async def federation_send(
        namespace: str, body: dict, request: Request, environment: str | None = None
    ):
        st = state(request)
        env = environment or settings.default_env
        ns_id = await st.registry.ensure_namespace(namespace, env)
        try:
            result = await st.federation.send_federated(
                ns_id,
                sender=body.get("sender", "agents/system"),
                recipient=body["recipient"],
                payload=body.get("payload") or {},
                subject=body.get("subject"),
                idempotency_key=body.get("idempotencyKey"),
            )
        except KeyError as e:
            raise HTTPException(400, f"missing field: {e}") from e
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        except Exception as e:
            raise HTTPException(502, f"federation forward failed: {e}") from e
        return result

    @app.post("/v1/federation/inbound")
    async def federation_inbound(
        body: dict, request: Request, namespace: str | None = None
    ):
        st = state(request)
        ns = namespace or settings.default_namespace
        ns_id = await st.registry.ensure_namespace(ns, settings.default_env)
        try:
            return await st.federation.receive_inbound(ns_id, body)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e

    # --- AMTP 1.0 (Agentry-compatible surface) ---
    @app.post("/v1/messages")
    async def amtp_send_or_receive(body: dict, request: Request):
        """Federated send/receive — Agentry-compatible POST /v1/messages."""
        st = state(request)
        from ai_platform.federation.amtp import AMTPMessage

        try:
            msg = AMTPMessage.model_validate(body)
            # Inbound if all recipients are local domain
            local_only = True
            for r in msg.recipients:
                from ai_platform.federation.amtp import parse_address

                _, d = parse_address(r)
                if d and d != st.amtp.domain:
                    local_only = False
                    break
            ns_id = await st.registry.ensure_namespace(
                settings.default_namespace, settings.default_env
            )
            st.amtp.default_namespace = ns_id
            return await st.amtp.send(msg, namespace_id=ns_id)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e

    @app.get("/v1/messages/{message_id}/status")
    async def amtp_message_status(message_id: str, request: Request):
        st = state(request)
        status = await st.amtp.get_status(message_id)
        return status.model_dump(mode="json")

    @app.get("/v1/capabilities/{domain}")
    async def amtp_capabilities(domain: str, request: Request):
        st = state(request)
        caps = await st.amtp.capabilities(domain)
        return caps.model_dump(mode="json")

    @app.get("/v1/capabilities")
    async def amtp_local_capabilities(request: Request):
        st = state(request)
        caps = await st.amtp.capabilities(st.amtp.domain)
        return caps.model_dump(mode="json")

    @app.get("/v1/discovery/agents")
    async def amtp_discovery_agents(
        request: Request,
        delivery_mode: str | None = None,
        active_only: bool = False,
    ):
        st = state(request)
        agents = await st.amtp.list_agents(active_only=active_only)
        if delivery_mode:
            agents = [a for a in agents if a.get("deliveryMode") == delivery_mode]
        return {"agents": agents, "domain": st.amtp.domain}

    @app.post("/v1/admin/agents")
    async def amtp_admin_register_agent(body: dict, request: Request):
        st = state(request)
        admin = request.headers.get("X-Admin-Key")
        if admin != st.amtp.admin_key:
            raise HTTPException(401, "invalid admin key")
        from ai_platform.federation.amtp import LocalAmtpAgent

        agent = await st.amtp.register_agent(LocalAmtpAgent.model_validate(body))
        return agent.model_dump(mode="json")

    @app.post("/v1/admin/schemas")
    async def amtp_admin_put_schema(body: dict, request: Request):
        st = state(request)
        admin = request.headers.get("X-Admin-Key")
        if admin != st.amtp.admin_key:
            raise HTTPException(401, "invalid admin key")
        schema_id = body.get("schemaId") or body.get("id")
        if not schema_id:
            raise HTTPException(400, "schemaId required")
        return await st.amtp.schemas.put(
            schema_id, body.get("definition") or {}, body.get("version", "1.0")
        )

    @app.get("/v1/admin/schemas")
    async def amtp_admin_list_schemas(request: Request):
        st = state(request)
        return {"schemas": await st.amtp.schemas.list()}

    @app.post("/v1/amtp/peers")
    async def amtp_register_peer(body: dict, request: Request):
        st = state(request)
        caps = st.amtp.register_peer(
            body["domain"], body["gateway"], body.get("auth")
        )
        return caps.model_dump(mode="json")

    @app.get("/v1/amtp/dns-txt")
    async def amtp_dns_txt(request: Request, gateway: str = "https://localhost:8080"):
        st = state(request)
        return {
            "name": f"_amtp.{st.amtp.domain}",
            "type": "TXT",
            "value": st.amtp.dns_txt_record(gateway),
        }

    # --- Context Graph ---
    @app.post("/v1/{namespace:path}/traces")
    async def create_trace(
        namespace: str, body: dict, request: Request, environment: str | None = None
    ):
        st = state(request)
        from ai_platform.context_graph.service import CreateTraceRequest

        env = environment or settings.default_env
        ns_id = await st.registry.ensure_namespace(namespace, env)
        req = CreateTraceRequest.model_validate(body)
        trace = await st.context_graph.create_trace(ns_id, req)
        return trace.model_dump(mode="json")

    @app.get("/v1/{namespace:path}/traces")
    async def list_traces(
        namespace: str, request: Request, environment: str | None = None, limit: int = 50
    ):
        st = state(request)
        env = environment or settings.default_env
        ns_id = await st.registry.ensure_namespace(namespace, env)
        traces = await st.context_graph.list_traces(ns_id, limit)
        return {"traces": [t.model_dump(mode="json") for t in traces]}

    @app.get("/v1/traces/{trace_id}")
    async def get_trace(trace_id: str, request: Request):
        st = state(request)
        trace = await st.context_graph.get_trace(trace_id)
        if not trace:
            raise HTTPException(404, detail="Trace not found")
        return trace.model_dump(mode="json")

    @app.post("/v1/{namespace:path}/traces/precedents")
    async def query_precedents(
        namespace: str, body: dict, request: Request, environment: str | None = None
    ):
        st = state(request)
        from ai_platform.context_graph.service import PrecedentQuery

        env = environment or settings.default_env
        ns_id = await st.registry.ensure_namespace(namespace, env)
        query = PrecedentQuery.model_validate(body)
        results = await st.context_graph.query_precedents(ns_id, query)
        return {"precedents": [t.model_dump(mode="json") for t in results]}

    @app.post("/v1/traces/{from_id}/link/{to_id}")
    async def link_traces(
        from_id: str, to_id: str, request: Request, link_type: str = "based_on_precedent"
    ):
        st = state(request)
        link = await st.context_graph.link_traces(from_id, to_id, link_type)
        return link.model_dump(mode="json")

    @app.get("/v1/traces/{trace_id}/links")
    async def get_trace_links(trace_id: str, request: Request):
        st = state(request)
        return {"links": await st.context_graph.get_linked(trace_id)}

    # --- Agent Discovery ---
    @app.post("/v1/{namespace:path}/discovery/register")
    async def register_capability(
        namespace: str, body: dict, request: Request, environment: str | None = None
    ):
        st = state(request)
        from ai_platform.discovery.service import RegisterCapabilityRequest

        env = environment or settings.default_env
        ns_id = await st.registry.ensure_namespace(namespace, env)
        req = RegisterCapabilityRequest.model_validate(body)
        record = await st.discovery.register(ns_id, req)
        return record.model_dump(mode="json")

    @app.post("/v1/{namespace:path}/discovery/find")
    async def discover_agents(
        namespace: str, body: dict, request: Request, environment: str | None = None
    ):
        st = state(request)
        from ai_platform.discovery.service import DiscoveryQuery

        env = environment or settings.default_env
        ns_id = await st.registry.ensure_namespace(namespace, env)
        query = DiscoveryQuery.model_validate(body)
        agents = await st.discovery.discover(ns_id, query)
        return {"agents": [a.model_dump(mode="json") for a in agents]}

    @app.get("/v1/{namespace:path}/discovery/agents")
    async def list_discovered_agents(
        namespace: str, request: Request, environment: str | None = None
    ):
        st = state(request)
        env = environment or settings.default_env
        ns_id = await st.registry.ensure_namespace(namespace, env)
        agents = await st.discovery.list_agents(ns_id)
        return {"agents": [a.model_dump(mode="json") for a in agents]}

    @app.post("/v1/{namespace:path}/discovery/sync")
    async def sync_discovery_from_bundle(
        namespace: str, request: Request, environment: str | None = None
    ):
        st = state(request)
        env = environment or settings.default_env
        ns_id = await st.registry.ensure_namespace(namespace, env)
        published = await st.registry.list_published(ns_id)
        bundle = _bundle_index(published)
        count = await st.discovery.sync_from_bundle(ns_id, bundle)
        return {"synced": count}

    @app.post("/v1/{namespace:path}/discovery/route")
    async def route_best_agent(
        namespace: str, body: dict, request: Request, environment: str | None = None
    ):
        st = state(request)
        env = environment or settings.default_env
        ns_id = await st.registry.ensure_namespace(namespace, env)
        caps = body.get("capabilities", [])
        best = await st.discovery.route_best(ns_id, caps)
        if not best:
            raise HTTPException(404, detail="No agent matched capabilities")
        return best.model_dump(mode="json")

    # --- Dynamic Workflows ---
    @app.post("/v1/{namespace:path}/workflows/plan")
    async def plan_dynamic_workflow(
        namespace: str, body: dict, request: Request, environment: str | None = None
    ):
        st = state(request)
        from ai_platform.workflow.dynamic import PlanRequest

        env = environment or settings.default_env
        ns_id = await st.registry.ensure_namespace(namespace, env)
        plan_req = PlanRequest.model_validate(body)
        # Prefer discovered agents if none provided
        discovery_hits = None
        if not plan_req.available_agents:
            from ai_platform.discovery.service import DiscoveryQuery

            found = await st.discovery.discover(
                ns_id, DiscoveryQuery(capabilities=["executor", "research"], limit=5)
            )
            discovery_hits = [a.agent_ref for a in found]
        published = await st.registry.list_published(ns_id)
        bundle = _bundle_index(published)
        result = await st.dynamic_workflows.plan_and_run(
            ns_id,
            namespace.split("/")[0],
            plan_req,
            bundle,
            discovery_hits=discovery_hits,
        )
        return result.model_dump(mode="json")

    @app.get("/v1/workflows/dynamic/{workflow_id}")
    async def get_dynamic_workflow(workflow_id: str, request: Request):
        st = state(request)
        data = await st.dynamic_workflows.get(workflow_id)
        if not data:
            raise HTTPException(404, detail="Dynamic workflow not found")
        return data

    # --- Resources list (for Admin Console) ---
    @app.get("/v1/{namespace:path}/resources")
    async def list_resources(
        namespace: str, request: Request, environment: str | None = None
    ):
        st = state(request)
        env = environment or settings.default_env
        ns_id = await st.registry.ensure_namespace(namespace, env)
        published = await st.registry.list_published(ns_id)
        return {
            "resources": [
                {
                    "kind": v.kind,
                    "name": v.name,
                    "version": v.version,
                    "spec": v.spec_json,
                }
                for v in published
                if v.kind and v.name
            ]
        }

    @app.get("/v1/regions")
    async def list_regions(request: Request):
        st = state(request)
        regions = await st.region_service.list_regions()
        return {"regions": [r.model_dump() for r in regions]}

    @app.post("/v1/regions")
    async def register_region(body: RegisterRegionBody, request: Request):
        st = state(request)
        region_id = await st.region_service.register_region(
            body.name, body.endpoint, body.data_residency, body.is_primary
        )
        return {"regionId": region_id, "name": body.name}

    @app.post("/v1/regions/{name}/failover")
    async def region_failover(name: str, request: Request):
        st = state(request)
        new_primary = await st.region_service.failover(name)
        if not new_primary:
            raise HTTPException(503, detail="No failover region available")
        return {"failed": name, "newPrimary": new_primary.model_dump()}

    @app.post("/v1/edge/register")
    async def register_edge(body: RegisterEdgeBody, request: Request):
        st = state(request)
        ns_id = await st.registry.ensure_namespace(body.namespace, body.environment)
        node_id = await st.region_service.register_edge_node(
            ns_id,
            body.region,
            None,
            body.bundle_cache_path,
            body.metadata,
        )
        return {"nodeId": node_id, "namespaceId": ns_id, "mode": "edge"}

    @app.post("/v1/edge/{node_id}/telemetry")
    async def edge_telemetry(node_id: str, body: TelemetryBody, request: Request):
        st = state(request)
        await st.region_service.record_edge_telemetry(node_id)
        return {"received": len(body.events), "nodeId": node_id}

    @app.get("/v1/compliance/packs")
    async def list_compliance_packs(request: Request):
        st = state(request)
        packs = st.compliance_service.list_packs()
        return {
            "packs": [
                {
                    "id": p.id,
                    "name": p.name,
                    "framework": p.framework,
                    "version": p.version,
                    "description": p.description,
                }
                for p in packs
            ]
        }

    @app.post("/v1/{namespace:path}/compliance/install")
    async def install_compliance_pack(
        namespace: str, body: InstallComplianceBody, request: Request, environment: str | None = None
    ):
        st = state(request)
        env = environment or settings.default_env
        ns_id = await st.registry.ensure_namespace(namespace, env)
        principal = _auth_principal(request, st)
        result = await st.compliance_service.install_pack(
            body.pack_id, ns_id, namespace, body.installed_by or principal
        )
        return result

    @app.post("/v1/{namespace:path}/model-routes/{name}/tune")
    async def tune_model_route(
        namespace: str, name: str, request: Request, environment: str | None = None, apply: bool = True
    ):
        st = state(request)
        env = environment or settings.default_env
        ns_id = await st.registry.ensure_namespace(namespace, env)
        ver = await st.registry.get_published_version(ns_id, ResourceKind.MODEL_ROUTE, name)
        if not ver:
            raise HTTPException(404, detail="ModelRoute not found or not published")
        from ai_platform.core.models import ModelRouteSpec

        route_spec = ModelRouteSpec.model_validate(ver.spec_json)
        route_ref = f"models/{name}"
        if apply:
            result = await st.route_tuner.tune_and_apply_resource(
                route_ref, ns_id, namespace, route_spec, st.registry
            )
        else:
            result = await st.route_tuner.tune(route_ref, ns_id, route_spec)
        return result.model_dump()

    @app.post("/v1/auth/login")
    async def login(body: LoginBody, request: Request):
        st = state(request)
        return await st.sso_service.login(body.org_id, body.email, body.display_name)

    @app.get("/v1/marketplace/plugins")
    async def list_marketplace_plugins(request: Request, tier: str | None = None):
        st = state(request)
        plugins = await st.marketplace_catalog.list_plugins(tier)
        return {"plugins": plugins}

    @app.post("/v1/marketplace/plugins")
    async def publish_marketplace_plugin(body: PublishPluginBody, request: Request):
        st = state(request)
        from ai_platform.core.models import PluginManifest

        manifest = PluginManifest.model_validate(body.manifest)
        plugin_id = await st.marketplace_catalog.publish_plugin(body.name, manifest)
        return {"pluginId": plugin_id, "name": body.name, "version": manifest.version}

    @app.post("/v1/{namespace:path}/marketplace/install")
    async def install_marketplace_plugin(
        namespace: str, body: InstallPluginBody, request: Request, environment: str | None = None
    ):
        st = state(request)
        env = environment or settings.default_env
        ns_id = await st.registry.ensure_namespace(namespace, env)
        principal = _auth_principal(request, st)
        result = await st.marketplace_service.install(
            ns_id,
            namespace,
            body.plugin_name,
            body.version,
            body.installed_by or principal,
        )
        return result

    @app.post("/v1/{namespace:path}/git-sync")
    async def git_sync(namespace: str, body: GitSyncBody, request: Request, environment: str | None = None):
        st = state(request)
        env = environment or settings.default_env
        ns_id = await st.registry.ensure_namespace(namespace, env)
        from pathlib import Path

        result = await st.git_sync.sync_from_directory(
            ns_id, namespace, Path(body.directory), body.publish, body.author
        )
        return result.model_dump()

    @app.post("/v1/{namespace:path}/git-export")
    async def git_export(
        namespace: str, request: Request, directory: str = "./export", environment: str | None = None
    ):
        st = state(request)
        env = environment or settings.default_env
        ns_id = await st.registry.ensure_namespace(namespace, env)
        from pathlib import Path

        count = await st.git_sync.export_to_directory(ns_id, namespace, Path(directory))
        return {"exported": count, "directory": directory}

    @app.get("/scim/v2/Users")
    async def scim_list_users(request: Request, org_id: str = "default-org"):
        st = state(request)
        return await st.scim_service.list_users(org_id)

    @app.post("/scim/v2/Users")
    async def scim_create_user(body: ScimUserBody, request: Request, org_id: str = "default-org"):
        st = state(request)
        from ai_platform.core.models import ScimUserPayload

        payload = ScimUserPayload.model_validate(body.model_dump())
        return await st.scim_service.create_user(org_id, payload)

    @app.delete("/scim/v2/Users/{user_id}")
    async def scim_delete_user(user_id: str, request: Request):
        st = state(request)
        await st.scim_service.delete_user(user_id)
        return {"deleted": True}

    @app.post("/v1/{namespace:path}/terraform/export")
    async def terraform_export(
        namespace: str, request: Request, directory: str = "./terraform", environment: str | None = None
    ):
        st = state(request)
        env = environment or settings.default_env
        ns_id = await st.registry.ensure_namespace(namespace, env)
        published = await st.registry.list_published(ns_id)
        from pathlib import Path
        from ai_platform.terraform.export import write_terraform_files

        count = write_terraform_files(published, namespace, Path(directory))
        return {"exported": count, "directory": directory}

    @app.post("/v1/nodes/register")
    async def register_node(body: RegisterNodeBody, request: Request):
        st = state(request)
        ns_id = await st.registry.ensure_namespace(body.namespace, body.environment)
        node_id = await st.registry.register_runtime_node(ns_id, body.node_type, body.metadata)
        return {"nodeId": node_id, "namespaceId": ns_id, "publicKey": st.public_key_hex}

    @app.put("/v1/{namespace:path}/{kind}/{name}/versions/{version}")
    async def upsert_version(
        namespace: str,
        kind: str,
        name: str,
        version: str,
        body: ResourceUpsertBody,
        request: Request,
    ):
        st = state(request)
        ns_id = await st.registry.ensure_namespace(namespace, settings.default_env)
        resource = _parse_resource(body, namespace)
        resource.metadata.name = name
        resource.metadata.version = version
        resource.kind = ResourceKind(kind)

        errors = validate_platform_resource(resource)
        if errors:
            raise HTTPException(400, detail={"validationErrors": errors})

        ver = await st.registry.upsert_resource_version(ns_id, resource)
        return {"versionId": ver.id, "kind": kind, "name": name, "version": version}

    @app.post("/v1/{namespace:path}/{kind}/{name}/publish")
    async def publish_resource(
        namespace: str,
        kind: str,
        name: str,
        body: PublishBody,
        request: Request,
        environment: str | None = None,
    ):
        st = state(request)
        env = environment or settings.default_env
        ns_id = await st.registry.ensure_namespace(namespace, env)
        published = await st.registry.list_published(ns_id)
        bundle = _bundle_index(published)
        st.policy_engine.load_from_bundle(bundle)

        from ai_platform.publish.service import PublishGateError

        principal = _auth_principal(request, st)
        try:
            result = await st.publish_service.publish_with_gates(
                ns_id,
                namespace,
                ResourceKind(kind),
                name,
                body.version,
                principal=body.principal if body.principal != "anonymous" else principal,
                environment=env,
                bundle=bundle,
                eval_suite_ref=body.eval_suite_ref,
            )
        except PublishGateError as e:
            raise HTTPException(403, detail={"gate": e.reason, **e.details}) from e
        except ValueError as e:
            raise HTTPException(404, detail=str(e)) from e

        return result

    @app.post("/v1/{namespace:path}/promote")
    async def promote_environment(namespace: str, body: PromoteBody, request: Request):
        st = state(request)
        ns_id = await st.registry.ensure_namespace(namespace, body.from_env)
        published = await st.registry.list_published(ns_id)
        manifest = st.bundler.compile(f"{namespace:path}/{body.to_env}", body.to_env, published)

        promo_id = await st.promotion_service.request_promotion(
            ns_id, body.from_env, body.to_env, body.requested_by, manifest.bundle_hash
        )
        env_spec = st.promotion_service.get_environment_spec(
            _bundle_index(published), body.to_env
        )
        if env_spec and env_spec.require_approval:
            return {"promotionId": promo_id, "status": "pending_approval"}
        await st.promotion_service.approve_promotion(promo_id, body.requested_by)
        count = await st.promotion_service.promote_resources(namespace, body.from_env, body.to_env)
        return {"promotionId": promo_id, "status": "completed", "resourcesPromoted": count}

    @app.post("/v1/promotions/{promo_id}/approve")
    async def approve_promotion(promo_id: str, body: ApprovePromotionBody, request: Request):
        st = state(request)
        promo = await st.promotion_service.approve_promotion(promo_id, body.approved_by)
        namespace_path = settings.default_namespace
        count = await st.promotion_service.promote_resources(
            namespace_path, promo["from_env"], promo["to_env"]
        )
        return {"promotionId": promo_id, "status": "completed", "resourcesPromoted": count}

    @app.post("/v1/workflows/runs/{run_id}/approve")
    async def approve_workflow(run_id: str, body: WorkflowApproveBody, request: Request):
        st = state(request)
        state_obj = await st.workflow_engine.approve(run_id, body.decision)
        return state_obj.model_dump()

    @app.post("/v1/workflows/runs/{run_id}/resume")
    async def resume_workflow(
        run_id: str, request: Request, namespace: str | None = None, environment: str | None = None
    ):
        st = state(request)
        ns_path = namespace or settings.default_namespace
        env = environment or settings.default_env
        ns_id = await st.registry.ensure_namespace(ns_path, env)
        published = await st.registry.list_published(ns_id)
        bundle = _bundle_index(published)
        state_obj = await st.workflow_engine.resume(
            run_id, bundle, ns_path.split("/")[0], ns_id
        )
        return state_obj.model_dump()

    @app.get("/v1/bundles/{environment}")
    async def get_bundle(environment: str, request: Request, namespace: str | None = None):
        st = state(request)
        ns_path = namespace or settings.default_namespace
        ns_id = await st.registry.ensure_namespace(ns_path, environment)
        published = await st.registry.list_published(ns_id)
        manifest = st.bundler.compile(f"{ns_path}/{environment}", environment, published)
        return {
            "namespace": manifest.namespace,
            "environment": manifest.environment,
            "bundleHash": manifest.bundle_hash,
            "signature": manifest.signature,
            "publicKey": st.public_key_hex,
            "resources": manifest.resources,
            "createdAt": manifest.created_at.isoformat(),
        }

    @app.get("/v1/{namespace:path}/{kind}/{name}")
    async def get_resource(namespace: str, kind: str, name: str, request: Request):
        st = state(request)
        ns_id = await st.registry.ensure_namespace(namespace, settings.default_env)
        ver = await st.registry.get_published_version(ns_id, ResourceKind(kind), name)
        if not ver:
            raise HTTPException(404, detail="Resource not found or not published")
        return {
            "kind": kind,
            "name": name,
            "version": ver.version,
            "spec": ver.spec_json,
            "status": ver.status_json,
        }

    return app


def run() -> None:
    import uvicorn

    settings = Settings()
    uvicorn.run(create_app(settings), host=settings.api_host, port=settings.api_port)
