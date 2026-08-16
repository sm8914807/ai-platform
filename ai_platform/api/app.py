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


class OidcStartBody(BaseModel):
    code_challenge: str = Field(alias="codeChallenge")
    org_id: str | None = Field(default=None, alias="orgId")
    redirect_uri: str | None = Field(default=None, alias="redirectUri")

    model_config = {"populate_by_name": True}


class OidcCallbackBody(BaseModel):
    code: str
    state: str
    code_verifier: str = Field(alias="codeVerifier")
    org_id: str | None = Field(default=None, alias="orgId")

    model_config = {"populate_by_name": True}


class PublishPluginBody(BaseModel):
    name: str
    manifest: dict[str, Any]


class InstallPluginBody(BaseModel):
    plugin_name: str = Field(alias="pluginName")
    version: str | None = None
    installed_by: str | None = Field(default=None, alias="installedBy")

    model_config = {"populate_by_name": True}


class NamespaceBody(BaseModel):
    path: str
    environment: str | None = None


class UnpublishBody(BaseModel):
    principal: str = "anonymous"


class GitSyncBody(BaseModel):
    directory: str
    publish: bool = True
    author: str | None = None


class GitExportBody(BaseModel):
    directory: str = "./export"


class TerraformExportBody(BaseModel):
    directory: str = "./terraform"
    write: bool = True


class ScimUserBody(BaseModel):
    schemas: list[str] = Field(default_factory=lambda: ["urn:ietf:params:scim:schemas:core:2.0:User"])
    userName: str
    name: dict[str, str] = Field(default_factory=dict)
    # SCIM emails carry mixed types (e.g. {"value": str, "primary": bool}).
    emails: list[dict[str, Any]] = Field(default_factory=list)
    active: bool = True
    externalId: str | None = None


class McpListBody(BaseModel):
    tool_ref: str | None = Field(default=None, alias="toolRef")
    config: dict[str, Any] | None = None

    model_config = {"populate_by_name": True}


class McpCallBody(BaseModel):
    tool_ref: str | None = Field(default=None, alias="toolRef")
    config: dict[str, Any] | None = None
    tool_name: str | None = Field(default=None, alias="toolName")
    arguments: dict[str, Any] = Field(default_factory=dict)

    model_config = {"populate_by_name": True}


def _auth_principal(request: Request, st: AppState) -> str:
    auth = request.headers.get("Authorization")
    ctx = st.sso_service.authenticate(auth)
    if ctx:
        return ctx.principal
    if st.auth_required:
        raise HTTPException(status_code=401, detail="authentication required")
    return "anonymous"


async def _record_audit(
    st: AppState,
    *,
    org_id: str,
    action: str,
    actor_id: str | None = None,
    resource_ref: str | None = None,
    payload: dict[str, Any] | None = None,
    ip: str | None = None,
) -> None:
    from ai_platform.core.models import AuditEvent

    await st.registry.append_audit(
        AuditEvent(
            id=new_id("audit"),
            org_id=org_id,
            actor_id=actor_id,
            action=action,
            resource_ref=resource_ref,
            payload=payload or {},
            ip=ip,
            created_at=datetime.now(timezone.utc),
        )
    )


def _is_public_path(path: str) -> bool:
    if path in {
        "/health",
        "/openapi.json",
        "/docs",
        "/redoc",
        "/v1/auth/login",
        "/v1/auth/config",
        "/v1/auth/oidc/start",
        "/v1/auth/oidc/callback",
    }:
        return True
    if path.startswith("/docs") or path.startswith("/redoc"):
        return True
    if path.startswith("/.well-known/"):
        return True
    # AMTP discovery endpoints stay reachable without a Studio session.
    if path in {"/v1/capabilities", "/v1/federation/info", "/v1/amtp/dns-txt", "/metrics"}:
        return True
    return False


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

    from ai_platform.api.prod_checks import assert_production_ready
    from ai_platform.db.backend import database_url, is_postgres

    assert_production_ready(settings)

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
        from ai_platform.telemetry.tracing import setup_tracing, shutdown_tracing

        setup_tracing(
            settings.otlp_service_name,
            settings.otlp_endpoint,
            environment=settings.default_env,
            service_version="0.8.0",
            console=settings.otlp_console,
            memory=settings.otlp_memory,
            force=True,
        )

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
            redis_url=settings.redis_url,
            governor_backend=settings.governor_backend,
            auth_secret=settings.auth_secret,
            auth_required=settings.auth_required,
            planner_mode=settings.planner_mode,
            oidc_issuer=settings.oidc_issuer,
            oidc_client_id=settings.oidc_client_id,
            oidc_client_secret=settings.oidc_client_secret,
            oidc_redirect_uri=settings.oidc_redirect_uri,
            oidc_scopes=settings.oidc_scopes,
            oidc_audience=settings.oidc_audience,
            allow_dev_login=settings.allow_dev_login,
            default_org_id=settings.default_namespace.split("/", 1)[0],
        )
        # Per-service migrate remains for sqlite unit paths / idempotent DDL
        await app.state.platform.workflow_engine.initialize()
        await app.state.platform.region_service.migrate()
        await app.state.platform.context_graph.migrate()
        await app.state.platform.discovery.migrate()
        await app.state.platform.dynamic_workflows.migrate()
        await app.state.platform.message_bus.migrate()
        await app.state.platform.secrets.migrate()
        await app.state.platform.git_sync.migrate()
        # Best-effort audit retention on boot (org = default namespace prefix).
        try:
            org_id = settings.default_namespace.split("/", 1)[0]
            await registry.purge_audit(
                org_id, retain_days=settings.audit_retention_days
            )
        except Exception:  # noqa: BLE001 — never block boot on retention
            pass
        try:
            yield
        finally:
            await sql.close()
            if backend == "postgres" and hasattr(registry, "close"):
                await registry.close()
            shutdown_tracing()

    app = FastAPI(title="AI Platform Control Plane", version="0.8.0", lifespan=lifespan)

    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import JSONResponse

    origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins or ["http://localhost:5173"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def require_auth(request: Request, call_next):
        if request.method == "OPTIONS":
            return await call_next(request)
        if _is_public_path(request.url.path):
            return await call_next(request)
        st = getattr(request.app.state, "platform", None)
        if st is None or not st.auth_required:
            return await call_next(request)
        ctx = st.sso_service.authenticate(request.headers.get("Authorization"))
        if ctx is None:
            return JSONResponse({"detail": "authentication required"}, status_code=401)
        request.state.auth = ctx
        return await call_next(request)

    @app.middleware("http")
    async def otel_http(request: Request, call_next):
        from ai_platform.telemetry.tracing import trace_http_middleware

        return await trace_http_middleware(request, call_next)

    def state(request: Request) -> AppState:
        return request.app.state.platform

    @app.get("/health")
    async def health(request: Request):
        st = getattr(request.app.state, "platform", None)
        from ai_platform.telemetry.tracing import tracing_status

        return {
            "status": "ok",
            "version": "0.8.0",
            "publicKey": bundler.public_key_hex,
            "registryBackend": st.backend if st else backend,
            "sqlBackend": st.sql.kind if st else backend,
            "federationDomain": settings.federation_domain,
            "env": settings.env,
            "governorBackend": (
                st.tool_governor.backend if st else settings.governor_backend
            ),
            "authRequired": settings.auth_required,
            "devLoginEnabled": settings.allow_dev_login,
            "tracing": tracing_status(),
            "otlpEndpointConfigured": bool(settings.otlp_endpoint),
        }

    @app.get("/v1/namespaces")
    async def list_namespaces(request: Request, environment: str | None = None):
        st = state(request)
        # Ensure the default workspace always appears in the switcher.
        await st.registry.ensure_namespace(
            settings.default_namespace, environment or settings.default_env
        )
        rows = await st.registry.list_namespaces()
        if environment:
            rows = [r for r in rows if r.get("env") == environment]
        # Deduplicate by path (prefer default env when multiple).
        by_path: dict[str, dict] = {}
        for r in rows:
            path = str(r.get("path") or "")
            if path and path not in by_path:
                by_path[path] = r
            elif path and r.get("env") == (environment or settings.default_env):
                by_path[path] = r
        return {
            "namespaces": list(by_path.values()),
            "default": settings.default_namespace,
            "environment": environment or settings.default_env,
        }

    @app.post("/v1/namespaces")
    async def ensure_namespace_route(body: NamespaceBody, request: Request):
        st = state(request)
        path = body.path.strip().strip("/")
        if "/" not in path:
            raise HTTPException(400, detail="path must be org/project")
        env = body.environment or settings.default_env
        ns_id = await st.registry.ensure_namespace(path, env)
        return {"id": ns_id, "path": path, "env": env}

    @app.get("/metrics")
    async def prometheus_metrics(request: Request, namespace: str | None = None):
        """Prometheus scrape endpoint for model-route metrics."""
        from fastapi.responses import PlainTextResponse

        st = state(request)
        ns_id = None
        if namespace:
            ns_id = await st.registry.ensure_namespace(namespace, settings.default_env)
        text = await st.metrics_collector.prometheus_text(ns_id)
        return PlainTextResponse(text, media_type="text/plain; version=0.0.4")

    @app.get("/v1/{namespace:path}/metrics/summary")
    async def metrics_summary(
        namespace: str,
        request: Request,
        environment: str | None = None,
        window: int = 500,
    ):
        st = state(request)
        env = environment or settings.default_env
        ns_id = await st.registry.ensure_namespace(namespace, env)
        return await st.metrics_collector.summarize_namespace(ns_id, window=window)

    @app.get("/v1/{namespace:path}/metrics/routes")
    async def metrics_routes(
        namespace: str,
        request: Request,
        environment: str | None = None,
        window: int = 500,
    ):
        st = state(request)
        env = environment or settings.default_env
        ns_id = await st.registry.ensure_namespace(namespace, env)
        summary = await st.metrics_collector.summarize_namespace(ns_id, window=window)
        return {"routes": summary["routes"], "overview": summary["overview"]}

    @app.get("/v1/{namespace:path}/metrics/routes/{name}")
    async def metrics_route_detail(
        namespace: str,
        name: str,
        request: Request,
        environment: str | None = None,
        window: int = 200,
    ):
        st = state(request)
        env = environment or settings.default_env
        ns_id = await st.registry.ensure_namespace(namespace, env)
        route_name = name if "/" in name else f"models/{name}"
        return await st.metrics_collector.summarize_route(route_name, ns_id, window=window)

    @app.get("/v1/{namespace:path}/metrics/recent")
    async def metrics_recent(
        namespace: str,
        request: Request,
        environment: str | None = None,
        route: str | None = None,
        limit: int = 50,
    ):
        st = state(request)
        env = environment or settings.default_env
        ns_id = await st.registry.ensure_namespace(namespace, env)
        return {
            "samples": await st.metrics_collector.recent(
                ns_id, route_name=route, limit=limit
            )
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
        await _record_audit(
            st,
            org_id=namespace.split("/", 1)[0],
            action="secret.put",
            actor_id=_auth_principal(request, st),
            resource_ref=f"secrets/{name}",
            payload={"namespace": namespace},
            ip=request.client.host if request.client else None,
        )
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

    async def _resolve_mcp_config(
        st: AppState, namespace: str, env: str, tool_ref: str | None, config: dict | None
    ) -> tuple[dict[str, Any], str | None]:
        """Return (config, namespace_id) for MCP list/call."""
        ns_id = await st.registry.ensure_namespace(namespace, env)
        if config:
            return dict(config), ns_id
        if not tool_ref:
            raise HTTPException(400, detail="toolRef or config is required")
        parts = tool_ref.split("/", 1)
        name = parts[1] if len(parts) == 2 else parts[0]
        published = await st.registry.list_published(ns_id)
        for v in published:
            if v.kind == "Tool" and v.name == name:
                cfg = dict(v.spec_json.get("config") or {})
                if v.spec_json.get("authRef"):
                    cfg.setdefault("authRef", v.spec_json["authRef"])
                manifest = v.spec_json.get("manifest") or {}
                cfg.setdefault("toolName", manifest.get("name") or name)
                return cfg, ns_id
        raise HTTPException(404, detail=f"Published tool not found: {tool_ref}")

    @app.post("/v1/{namespace:path}/mcp/list")
    async def mcp_list_tools(
        namespace: str, body: McpListBody, request: Request, environment: str | None = None
    ):
        """Discover tools from an MCP server (stdio or HTTP)."""
        from ai_platform.tool_host.mcp.client import McpClient, build_transport_from_config
        from ai_platform.tool_host.mcp.transports import McpTransportError
        from ai_platform.tool_host.sandbox import SandboxViolation

        st = state(request)
        env = environment or settings.default_env
        config, ns_id = await _resolve_mcp_config(st, namespace, env, body.tool_ref, body.config)
        config = await st.tool_sandbox.resolve_secrets(ns_id, config)
        try:
            if config.get("url") or str(config.get("transport", "")).lower() in {
                "http",
                "streamable-http",
                "sse",
                "https",
            }:
                st.tool_sandbox.check_url(str(config.get("url") or config.get("endpoint") or ""))
            elif config.get("command"):
                st.tool_sandbox.check_mcp_command(str(config["command"]))
            transport = build_transport_from_config(
                config, timeout_seconds=st.tool_sandbox.policy.timeout_seconds
            )
            client = McpClient(transport)
            try:
                tools = await client.list_tools()
                return {
                    "server": config.get("server"),
                    "serverInfo": client.server_info,
                    "tools": [
                        {
                            "name": t.name,
                            "description": t.description,
                            "inputSchema": t.input_schema,
                        }
                        for t in tools
                    ],
                }
            finally:
                await client.close()
        except (McpTransportError, SandboxViolation) as e:
            raise HTTPException(400, detail=str(e)) from e

    @app.post("/v1/{namespace:path}/mcp/call")
    async def mcp_call_tool(
        namespace: str, body: McpCallBody, request: Request, environment: str | None = None
    ):
        """Invoke one MCP tool (used by Studio probe / agent runtime)."""
        from ai_platform.core.models import ToolManifest, ToolSpec
        from ai_platform.tool_host.mcp.transports import McpTransportError
        from ai_platform.tool_host.sandbox import SandboxViolation

        st = state(request)
        env = environment or settings.default_env
        config, ns_id = await _resolve_mcp_config(st, namespace, env, body.tool_ref, body.config)
        tool_name = body.tool_name or config.get("toolName") or config.get("tool") or "tool"
        tool_resource = body.tool_ref or f"tools/{tool_name}"
        published = await st.registry.list_published(ns_id)
        st.policy_engine.load_from_bundle(_bundle_index(published))
        from ai_platform.core.models import PolicyContext

        decision = st.policy_engine.evaluate(
            PolicyContext(
                principal=_auth_principal(request, st),
                action="tool:invoke",
                resource=tool_resource if str(tool_resource).startswith("tools/") else f"tools/{tool_resource}",
                environment=env,
                org_id=namespace.split("/", 1)[0],
            )
        )
        if not decision.allowed:
            await _record_audit(
                st,
                org_id=namespace.split("/", 1)[0],
                action="policy.denied",
                actor_id=_auth_principal(request, st),
                resource_ref=str(tool_resource),
                payload={
                    "reason": decision.reason,
                    "matchedRule": decision.matched_rule,
                    "action": "tool:invoke",
                },
                ip=request.client.host if request.client else None,
            )
            raise HTTPException(
                403,
                detail={
                    "message": "policy denied",
                    "reason": decision.reason,
                    "matchedRule": decision.matched_rule,
                    "action": "tool:invoke",
                    "diagnosis": "A published Policy denied this MCP tool call.",
                },
            )
        spec = ToolSpec(
            adapter="mcp",
            manifest=ToolManifest(name=str(tool_name), inputSchema={}, outputSchema={}),
            config={**config, "toolName": tool_name},
            auth_ref=config.get("authRef"),
        )
        try:
            result = await st.tool_host.invoke(spec, body.arguments, namespace_id=ns_id)
            await _record_audit(
                st,
                org_id=namespace.split("/", 1)[0],
                action="mcp.call",
                actor_id=_auth_principal(request, st),
                resource_ref=str(tool_resource),
                payload={"toolName": tool_name, "latencyMs": result.latency_ms},
                ip=request.client.host if request.client else None,
            )
            return {"result": result.output, "latencyMs": result.latency_ms}
        except (McpTransportError, SandboxViolation, ValueError) as e:
            raise HTTPException(400, detail=str(e)) from e

    @app.post("/v1/{namespace:path}/execute")
    async def execute_resource(
        namespace: str, body: dict, request: Request, environment: str | None = None
    ):
        """Run a published agent or workflow once (Studio test runner / HITL seed).

        Set ``stream: true`` for SSE (``text/event-stream``) — multi-agent emits
        ``turn`` events live, then a final ``done`` / ``error``.
        """
        import json as _json

        from fastapi.responses import StreamingResponse

        from ai_platform.core.models import ExecutionRequest
        from ai_platform.orchestrator.engine import Orchestrator
        from ai_platform.telemetry.tracing import get_tracer

        st = state(request)
        env = environment or settings.default_env
        ns_id = await st.registry.ensure_namespace(namespace, env)
        if not isinstance(body.get("resource_ref"), str) or not body["resource_ref"]:
            raise HTTPException(400, detail="resource_ref is required")
        if body.get("input") is not None and not isinstance(body["input"], dict):
            raise HTTPException(400, detail="input must be an object")
        want_stream = bool(body.get("stream"))
        execution = ExecutionRequest.model_validate(
            {
                "resource_ref": body.get("resource_ref"),
                "input": body.get("input") or {},
                "session_id": body.get("session_id"),
                "trace_id": body.get("trace_id"),
                "stream": want_stream,
            }
        )
        if not (
            execution.resource_ref.startswith("agents/")
            or execution.resource_ref.startswith("workflows/")
        ):
            raise HTTPException(
                400,
                detail="Studio execution supports agents/ and workflows/ refs only",
            )
        published = await st.registry.list_published(ns_id)
        bundle = _bundle_index(published)
        orchestrator = Orchestrator(
            agent_engine=st.agent_engine,
            workflow_engine=st.workflow_engine,
            policy_engine=st.policy_engine,
        )
        bundle_key = f"{ns_id}:{env}"
        orchestrator.load_bundle(bundle_key, list(bundle.values()))
        principal = _auth_principal(request, st)
        org_id = namespace.split("/", 1)[0]
        tracer = get_tracer("ai-platform.api")
        with tracer.start_as_current_span("platform.execute") as span:
            span.set_attribute("resource.ref", execution.resource_ref)
            span.set_attribute("namespace", namespace)
            span.set_attribute("environment", env)
            span.set_attribute(
                "multi_agent", bool(body.get("multiAgent") or body.get("multi_agent"))
            )
            span.set_attribute("stream", want_stream)
            result = await orchestrator.execute(
                bundle_key,
                execution,
                principal=principal,
                environment=env,
                org_id=org_id,
                namespace_id=ns_id,
                multi_agent=bool(body.get("multiAgent") or body.get("multi_agent")),
                collaboration=body.get("collaboration"),
            )

        async def _audit_execute(final_payload: dict[str, Any] | None = None) -> None:
            payload = {
                "resourceRef": execution.resource_ref,
                "multiAgent": bool(body.get("multiAgent") or body.get("multi_agent")),
                "stream": want_stream,
            }
            if final_payload:
                if final_payload.get("message") == "policy denied" or final_payload.get(
                    "type"
                ) == "error":
                    payload["outcome"] = "denied_or_error"
                    payload["reason"] = final_payload.get("reason") or final_payload.get(
                        "message"
                    )
                    payload["matchedRule"] = final_payload.get("matchedRule")
                else:
                    payload["outcome"] = final_payload.get("status") or final_payload.get(
                        "type"
                    )
            await _record_audit(
                st,
                org_id=org_id,
                action="resource.execute",
                actor_id=principal,
                resource_ref=execution.resource_ref,
                payload=payload,
                ip=request.client.host if request.client else None,
            )
            if final_payload and (
                final_payload.get("message") == "policy denied"
                or (
                    isinstance(final_payload.get("data"), dict)
                    and final_payload["data"].get("message") == "policy denied"
                )
            ):
                data = (
                    final_payload["data"]
                    if isinstance(final_payload.get("data"), dict)
                    else final_payload
                )
                await _record_audit(
                    st,
                    org_id=org_id,
                    action="policy.denied",
                    actor_id=principal,
                    resource_ref=execution.resource_ref,
                    payload={
                        "reason": data.get("reason"),
                        "matchedRule": data.get("matchedRule"),
                        "action": data.get("action"),
                        "diagnosis": data.get("diagnosis"),
                    },
                    ip=request.client.host if request.client else None,
                )

        if want_stream:

            async def event_gen():
                final_data: dict[str, Any] | None = None
                async for event in result:
                    dumped = (
                        event.model_dump(mode="json")
                        if hasattr(event, "model_dump")
                        else event
                    )
                    if isinstance(dumped, dict) and dumped.get("type") in {
                        "done",
                        "error",
                    }:
                        final_data = dumped.get("data") if isinstance(
                            dumped.get("data"), dict
                        ) else dumped
                        if dumped.get("type") == "error" and isinstance(final_data, dict):
                            final_data = {**final_data, "type": "error"}
                    yield f"data: {_json.dumps(dumped)}\n\n"
                await _audit_execute(final_data)
                yield "data: {\"type\":\"stream_end\"}\n\n"

            return StreamingResponse(
                event_gen(),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "X-Accel-Buffering": "no",
                },
            )

        if hasattr(result, "model_dump"):
            dumped = result.model_dump(mode="json")
        else:
            dumped = result
        audit_payload = dumped.get("data") if isinstance(dumped, dict) else None
        if isinstance(dumped, dict) and dumped.get("type") == "error":
            audit_payload = {
                **(audit_payload if isinstance(audit_payload, dict) else {}),
                "type": "error",
            }
        await _audit_execute(audit_payload if isinstance(audit_payload, dict) else None)
        return dumped

    @app.post("/v1/{namespace:path}/{kind}/{name}/unpublish")
    async def unpublish_resource(
        namespace: str,
        kind: str,
        name: str,
        request: Request,
        body: UnpublishBody = UnpublishBody(),
        environment: str | None = None,
    ):
        st = state(request)
        env = environment or settings.default_env
        ns_id = await st.registry.ensure_namespace(namespace, env)
        try:
            await st.registry.unpublish(ns_id, ResourceKind(kind), name)
        except ValueError as e:
            raise HTTPException(404, detail=str(e)) from e
        await _record_audit(
            st,
            org_id=namespace.split("/", 1)[0],
            action="resource.unpublished",
            actor_id=_auth_principal(request, st),
            resource_ref=f"{kind}/{name}",
            payload={"environment": env},
            ip=request.client.host if request.client else None,
        )
        return {"unpublished": True, "kind": kind, "name": name}

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

    @app.get("/v1/{namespace:path}/audit")
    async def list_audit_events(
        namespace: str,
        request: Request,
        limit: int = 50,
        action: str | None = None,
    ):
        """Org-scoped activity log (publish, login, secrets, promotions, execute, …)."""
        st = state(request)
        _ = await st.registry.ensure_namespace(namespace, settings.default_env)
        org_id = namespace.split("/", 1)[0]
        events = await st.registry.list_audit(
            org_id, limit=min(max(limit, 1), 200), action=action
        )
        return {
            "orgId": org_id,
            "events": [e.model_dump(mode="json", by_alias=True) for e in events],
            "count": len(events),
            "retentionDays": settings.audit_retention_days,
        }

    @app.post("/v1/{namespace:path}/audit/purge")
    async def purge_audit_events(namespace: str, request: Request):
        """Delete audit events older than PLATFORM_AUDIT_RETENTION_DAYS for this org."""
        st = state(request)
        _ = await st.registry.ensure_namespace(namespace, settings.default_env)
        org_id = namespace.split("/", 1)[0]
        deleted = await st.registry.purge_audit(
            org_id, retain_days=settings.audit_retention_days
        )
        await _record_audit(
            st,
            org_id=org_id,
            action="audit.purged",
            actor_id=_auth_principal(request, st),
            payload={
                "deleted": deleted,
                "retainDays": settings.audit_retention_days,
            },
            ip=request.client.host if request.client else None,
        )
        return {
            "orgId": org_id,
            "deleted": deleted,
            "retainDays": settings.audit_retention_days,
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
        await _record_audit(
            st,
            org_id=settings.default_namespace.split("/", 1)[0],
            action="region.registered",
            actor_id=_auth_principal(request, st),
            resource_ref=f"regions/{body.name}",
            payload={"endpoint": body.endpoint, "isPrimary": body.is_primary},
            ip=request.client.host if request.client else None,
        )
        return {"regionId": region_id, "name": body.name}

    @app.post("/v1/regions/{name}/failover")
    async def region_failover(name: str, request: Request):
        st = state(request)
        new_primary = await st.region_service.failover(name)
        if not new_primary:
            raise HTTPException(503, detail="No failover region available")
        await _record_audit(
            st,
            org_id=settings.default_namespace.split("/", 1)[0],
            action="region.failover",
            actor_id=_auth_principal(request, st),
            resource_ref=f"regions/{name}",
            payload={"newPrimary": new_primary.name},
            ip=request.client.host if request.client else None,
        )
        return {"failed": name, "newPrimary": new_primary.model_dump()}

    @app.post("/v1/regions/{name}/primary")
    async def region_set_primary(name: str, request: Request):
        st = state(request)
        regions = await st.region_service.list_regions()
        if not any(r.name == name for r in regions):
            raise HTTPException(404, detail="Region not found")
        await st.region_service.set_primary(name)
        primary = await st.region_service.get_primary()
        await _record_audit(
            st,
            org_id=settings.default_namespace.split("/", 1)[0],
            action="region.primary",
            actor_id=_auth_principal(request, st),
            resource_ref=f"regions/{name}",
            ip=request.client.host if request.client else None,
        )
        return {"primary": primary.model_dump() if primary else None}

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
        await _record_audit(
            st,
            org_id=body.namespace.split("/", 1)[0],
            action="edge.registered",
            actor_id=_auth_principal(request, st),
            resource_ref=f"edge/{node_id}",
            payload={"region": body.region, "namespaceId": ns_id},
            ip=request.client.host if request.client else None,
        )
        return {"nodeId": node_id, "namespaceId": ns_id, "mode": "edge"}

    @app.get("/v1/edge/nodes")
    async def list_edge_nodes(request: Request, limit: int = 100):
        st = state(request)
        nodes = await st.region_service.list_edge_nodes(limit=limit)
        return {"nodes": nodes, "count": len(nodes)}

    @app.get("/v1/edge/telemetry")
    async def list_edge_telemetry(
        request: Request,
        node_id: str | None = None,
        limit: int = 100,
        hours: int = 24,
        summary: bool = True,
    ):
        st = state(request)
        if summary:
            return await st.region_service.telemetry_summary(hours=hours)
        events = await st.region_service.list_edge_telemetry(
            node_id=node_id, limit=limit
        )
        return {"events": events, "count": len(events)}

    @app.post("/v1/edge/{node_id}/telemetry")
    async def edge_telemetry(node_id: str, body: TelemetryBody, request: Request):
        st = state(request)
        received = await st.region_service.record_edge_telemetry(node_id, body.events)
        return {"received": received, "nodeId": node_id}

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

    @app.get("/v1/auth/config")
    async def auth_config(request: Request):
        st = state(request)
        return st.sso_service.auth_config()

    @app.post("/v1/auth/login")
    async def login(body: LoginBody, request: Request):
        st = state(request)
        try:
            result = await st.sso_service.login(body.org_id, body.email, body.display_name)
        except PermissionError as e:
            raise HTTPException(403, detail=str(e)) from e
        user = result.get("user") or {}
        await _record_audit(
            st,
            org_id=body.org_id,
            action="auth.login",
            actor_id=user.get("id") or body.email,
            resource_ref=f"users/{body.email}",
            payload={"provider": result.get("provider") or "dev"},
            ip=request.client.host if request.client else None,
        )
        return result

    @app.post("/v1/auth/oidc/start")
    async def oidc_start(body: OidcStartBody, request: Request):
        st = state(request)
        from ai_platform.auth.oidc_provider import OidcProviderError

        try:
            return await st.sso_service.begin_oidc(
                code_challenge=body.code_challenge,
                org_id=body.org_id,
                redirect_uri=body.redirect_uri,
            )
        except OidcProviderError as e:
            raise HTTPException(400, detail=str(e)) from e

    @app.post("/v1/auth/oidc/callback")
    async def oidc_callback(body: OidcCallbackBody, request: Request):
        st = state(request)
        from ai_platform.auth.oidc_provider import OidcProviderError

        try:
            result = await st.sso_service.complete_oidc(
                code=body.code,
                state=body.state,
                code_verifier=body.code_verifier,
                org_id=body.org_id,
            )
        except OidcProviderError as e:
            raise HTTPException(400, detail=str(e)) from e
        user = result.get("user") or {}
        org_id = body.org_id or settings.default_namespace.split("/", 1)[0]
        await _record_audit(
            st,
            org_id=org_id,
            action="auth.login",
            actor_id=user.get("id") or user.get("email"),
            resource_ref=f"users/{user.get('email') or 'oidc'}",
            payload={"provider": result.get("provider") or "oidc"},
            ip=request.client.host if request.client else None,
        )
        return result

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

    @app.get("/v1/{namespace:path}/git-sync/repos")
    async def list_git_repos(
        namespace: str, request: Request, environment: str | None = None
    ):
        st = state(request)
        env = environment or settings.default_env
        ns_id = await st.registry.ensure_namespace(namespace, env)
        repos = await st.git_sync.list_repos(ns_id)
        return {"repos": repos}

    @app.post("/v1/{namespace:path}/git-export")
    async def git_export(
        namespace: str,
        request: Request,
        body: GitExportBody = GitExportBody(),
        directory: str | None = None,
        environment: str | None = None,
    ):
        st = state(request)
        env = environment or settings.default_env
        ns_id = await st.registry.ensure_namespace(namespace, env)
        from pathlib import Path

        out_dir = directory or body.directory or "./export"
        count = await st.git_sync.export_to_directory(ns_id, namespace, Path(out_dir))
        return {"exported": count, "directory": out_dir}

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
        namespace: str,
        request: Request,
        body: TerraformExportBody = TerraformExportBody(),
        directory: str | None = None,
        environment: str | None = None,
    ):
        st = state(request)
        env = environment or settings.default_env
        ns_id = await st.registry.ensure_namespace(namespace, env)
        published = await st.registry.list_published(ns_id)
        from pathlib import Path

        from ai_platform.terraform.export import build_terraform_files, write_terraform_files

        out_dir = directory or body.directory or "./terraform"
        write = body.write
        files = build_terraform_files(published, namespace)
        resource_files = [
            n for n in files if n.endswith(".tf") and n not in {"provider.tf", "variables.tf"}
        ]
        if write:
            count = write_terraform_files(published, namespace, Path(out_dir))
        else:
            count = len(resource_files)
        return {
            "exported": count,
            "directory": out_dir if write else None,
            "wrote": write,
            "files": sorted(files.keys()),
            "preview": {
                k: files[k] for k in ("provider.tf", "variables.tf", "exported.json") if k in files
            },
        }

    @app.get("/v1/{namespace:path}/terraform/preview")
    async def terraform_preview(
        namespace: str, request: Request, environment: str | None = None
    ):
        st = state(request)
        env = environment or settings.default_env
        ns_id = await st.registry.ensure_namespace(namespace, env)
        published = await st.registry.list_published(ns_id)
        from ai_platform.terraform.export import build_terraform_files

        files = build_terraform_files(published, namespace)
        return {
            "namespace": namespace,
            "resourceCount": len(
                [n for n in files if n.endswith(".tf") and n not in {"provider.tf", "variables.tf"}]
            ),
            "files": files,
        }

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
        # Include the version under publish so eval can execute against it.
        draft = await st.registry.get_version(ns_id, ResourceKind(kind), name, body.version)
        if draft:
            bundle[f"{kind}:{name}"] = {
                "kind": kind,
                "name": name,
                "spec": draft.spec_json,
            }
        st.policy_engine.load_from_bundle(bundle)

        from ai_platform.core.models import ExecutionRequest
        from ai_platform.orchestrator.engine import Orchestrator
        from ai_platform.publish.service import PublishGateError

        principal = _auth_principal(request, st)
        kind_slug = {
            "Agent": "agents",
            "Workflow": "workflows",
            "Prompt": "prompts",
            "Tool": "tools",
            "EvaluationSuite": "evaluationsuites",
        }.get(kind, kind.lower() + "s")
        target_ref = f"{kind_slug}/{name}"

        execute_fn = None
        if kind == "Agent":

            async def execute_fn(input_data: dict):  # noqa: F811
                orch = Orchestrator(
                    agent_engine=st.agent_engine,
                    workflow_engine=st.workflow_engine,
                    policy_engine=st.policy_engine,
                )
                bundle_key = f"{ns_id}:{env}:publish-eval"
                orch.load_bundle(bundle_key, list(bundle.values()))
                return await orch.execute(
                    bundle_key,
                    ExecutionRequest(resource_ref=target_ref, input=input_data, stream=False),
                    principal=principal,
                    environment=env,
                    org_id=namespace.split("/", 1)[0],
                    namespace_id=ns_id,
                )

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
                execute_fn=execute_fn,
                eval_suite_ref=body.eval_suite_ref,
            )
        except PublishGateError as e:
            raise HTTPException(403, detail={"gate": e.reason, **e.details}) from e
        except ValueError as e:
            raise HTTPException(404, detail=str(e)) from e

        return result

    @app.post("/v1/{namespace:path}/evaluations/run")
    async def run_evaluation(
        namespace: str,
        body: dict,
        request: Request,
        environment: str | None = None,
    ):
        """Run an EvaluationSuite against a target resource (publish-gate dry run)."""
        st = state(request)
        env = environment or settings.default_env
        ns_id = await st.registry.ensure_namespace(namespace, env)
        suite_ref = str(body.get("suiteRef") or body.get("evalSuiteRef") or "")
        target_ref = str(body.get("targetRef") or body.get("resourceRef") or "")
        target_version = str(body.get("targetVersion") or body.get("version") or "draft")
        if not suite_ref:
            raise HTTPException(400, detail="suiteRef is required")
        if not target_ref:
            raise HTTPException(400, detail="targetRef is required")

        published = await st.registry.list_published(ns_id)
        bundle = _bundle_index(published)

        # Prefer published suite; fall back to named draft version if provided.
        suite = st.eval_runner.load_suite_from_bundle(bundle, suite_ref)
        if not suite:
            suite_name = suite_ref.split("/", 1)[-1]
            suite_ver = body.get("suiteVersion")
            if suite_ver:
                draft = await st.registry.get_version(
                    ns_id, ResourceKind.EVALUATION_SUITE, suite_name, str(suite_ver)
                )
            else:
                resource = await st.registry.get_resource(
                    ns_id, ResourceKind.EVALUATION_SUITE, suite_name
                )
                draft = None
                if resource and resource.latest_version:
                    draft = await st.registry.get_version(
                        ns_id,
                        ResourceKind.EVALUATION_SUITE,
                        suite_name,
                        resource.latest_version,
                    )
            if draft:
                bundle[f"EvaluationSuite:{suite_name}"] = {
                    "kind": "EvaluationSuite",
                    "name": suite_name,
                    "spec": draft.spec_json,
                }
                suite = st.eval_runner.load_suite_from_bundle(
                    bundle, f"evaluationsuites/{suite_name}"
                )
        if not suite:
            raise HTTPException(404, detail=f"EvaluationSuite not found: {suite_ref}")

        from ai_platform.core.models import ExecutionRequest
        from ai_platform.orchestrator.engine import Orchestrator

        principal = _auth_principal(request, st)
        execute_fn = None
        if target_ref.startswith("agents/"):

            async def execute_fn(input_data: dict):  # noqa: F811
                orch = Orchestrator(
                    agent_engine=st.agent_engine,
                    workflow_engine=st.workflow_engine,
                    policy_engine=st.policy_engine,
                )
                bundle_key = f"{ns_id}:{env}:eval-run"
                orch.load_bundle(bundle_key, list(bundle.values()))
                return await orch.execute(
                    bundle_key,
                    ExecutionRequest(resource_ref=target_ref, input=input_data, stream=False),
                    principal=principal,
                    environment=env,
                    org_id=namespace.split("/", 1)[0],
                    namespace_id=ns_id,
                )

        result = await st.eval_runner.run_suite(
            suite, target_ref, target_version, execute_fn
        )
        return result.to_dict()

    @app.get("/v1/{namespace:path}/evaluations/recent")
    async def recent_evaluations(namespace: str, request: Request, limit: int = 20):
        st = state(request)
        return {"runs": st.eval_runner.recent_runs(limit=min(max(limit, 1), 100))}

    @app.post("/v1/{namespace:path}/promote")
    async def promote_environment(namespace: str, body: PromoteBody, request: Request):
        st = state(request)
        ns_id = await st.registry.ensure_namespace(namespace, body.from_env)
        published = await st.registry.list_published(ns_id)
        manifest = st.bundler.compile(f"{namespace}/{body.to_env}", body.to_env, published)

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
        await _record_audit(
            st,
            org_id=namespace.split("/", 1)[0],
            action="environment.promoted",
            actor_id=body.requested_by or _auth_principal(request, st),
            resource_ref=namespace,
            payload={
                "fromEnv": body.from_env,
                "toEnv": body.to_env,
                "resourcesPromoted": count,
            },
            ip=request.client.host if request.client else None,
        )
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
        try:
            state_obj = await st.workflow_engine.approve(run_id, body.decision)
        except ValueError as e:
            raise HTTPException(404, detail=str(e)) from e
        return state_obj.model_dump(by_alias=True)

    @app.get("/v1/workflows/inbox")
    async def workflow_inbox(
        request: Request,
        namespace: str | None = None,
        environment: str | None = None,
        limit: int = 50,
    ):
        st = state(request)
        ns_id = None
        if namespace:
            env = environment or settings.default_env
            ns_id = await st.registry.ensure_namespace(namespace, env)
        items = await st.workflow_engine.list_inbox(namespace_id=ns_id, limit=limit)
        return {"items": items, "count": len(items)}

    @app.get("/v1/workflows/runs/{run_id}")
    async def get_workflow_run(run_id: str, request: Request):
        st = state(request)
        item = await st.workflow_engine.get_run(run_id)
        if not item:
            raise HTTPException(404, detail="Run not found")
        return item

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
        try:
            state_obj = await st.workflow_engine.resume(
                run_id, bundle, ns_path.split("/")[0], ns_id
            )
        except ValueError as e:
            raise HTTPException(404, detail=str(e)) from e
        return state_obj.model_dump(by_alias=True)

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
