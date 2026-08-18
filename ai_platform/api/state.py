"""Application state."""

from ai_platform.agent.engine import AgentEngine
from ai_platform.auth.identity import IdentityStore, ScimService
from ai_platform.auth.sso import OidcValidator, SsoService
from ai_platform.bundler.compiler import BundleCompiler
from ai_platform.compliance.packs import CompliancePackService
from ai_platform.context_graph.service import ContextGraphService
from ai_platform.db.sql import SqlBackend, create_sql_backend
from ai_platform.discovery.service import AgentDiscoveryService
from ai_platform.evaluation.runner import EvaluationRunner
from ai_platform.federation.amtp import AMTPGateway
from ai_platform.federation.gateway import FederationGateway, FederationRegistry
from ai_platform.git_sync.service import GitSyncService
from ai_platform.governor.engine import ToolGovernor
from ai_platform.marketplace.service import MarketplaceCatalog, MarketplaceService
from ai_platform.messaging.bus import MessageBus
from ai_platform.model_router.tuner import RouteTuner
from ai_platform.observability.metrics import MetricsCollector
from ai_platform.policy.engine import PolicyEngine
from ai_platform.promotion.service import PromotionService
from ai_platform.publish.service import PublishService
from ai_platform.readiness.engine import ProductionReadinessEngine
from ai_platform.region.service import RegionService
from ai_platform.registry.store import RegistryStore
from ai_platform.secrets.manager import SecretsManager
from ai_platform.tool_host.sandbox import SandboxPolicy, SandboxedToolHost, ToolSandbox
from ai_platform.workflow.dynamic import DynamicWorkflowEngine
from ai_platform.workflow.engine import WorkflowEngine


class AppState:
    def __init__(
        self,
        registry: RegistryStore,
        bundler: BundleCompiler,
        db_path: str,
        *,
        federation_domain: str = "local.ai-platform",
        secrets_key: str | None = None,
        sandbox_timeout_seconds: float = 30.0,
        backend: str = "sqlite",
        sql: SqlBackend | None = None,
        database_url: str | None = None,
        redis_url: str | None = None,
        governor_backend: str = "auto",
        auth_secret: str | None = None,
        auth_required: bool = True,
        planner_mode: str = "auto",
        oidc_issuer: str | None = None,
        oidc_client_id: str | None = None,
        oidc_client_secret: str | None = None,
        oidc_redirect_uri: str = "http://localhost:5173/",
        oidc_scopes: str = "openid profile email",
        oidc_audience: str | None = None,
        allow_dev_login: bool = True,
        default_org_id: str = "default-org",
    ) -> None:
        self.registry = registry
        self.bundler = bundler
        self.db_path = db_path
        self.backend = backend
        self.sql = sql or create_sql_backend(
            db_path=db_path, database_url_override=database_url
        )
        self.public_key_hex: str = bundler.public_key_hex
        self.policy_engine = PolicyEngine()
        self.eval_runner = EvaluationRunner()
        self.publish_service = PublishService(registry, self.policy_engine, self.eval_runner)
        self.promotion_service = PromotionService(registry)

        # Prefer sql= when constructors support it; fall back to db_path.
        def _maybe(cls, **kwargs):
            try:
                return cls(sql=self.sql, db_path=db_path, **kwargs)
            except TypeError:
                return cls(db_path, **kwargs) if kwargs else cls(db_path)

        self.marketplace_catalog = _maybe(MarketplaceCatalog)
        self.marketplace_service = MarketplaceService(self.marketplace_catalog, registry)
        try:
            self.git_sync = GitSyncService(registry, db_path)
        except TypeError:
            self.git_sync = GitSyncService(registry, db_path)
        self.identity_store = _maybe(IdentityStore)
        self.scim_service = ScimService(self.identity_store)
        self.auth_required = auth_required
        oidc = OidcValidator(
            secret=auth_secret or secrets_key or "dev-platform-secret-change-in-prod"
        )
        oidc_provider = None
        if oidc_issuer and oidc_client_id:
            from ai_platform.auth.oidc_provider import OidcProvider

            oidc_provider = OidcProvider(
                issuer=oidc_issuer,
                client_id=oidc_client_id,
                client_secret=oidc_client_secret,
                redirect_uri=oidc_redirect_uri,
                scopes=oidc_scopes,
                audience=oidc_audience,
            )
        self.sso_service = SsoService(
            self.identity_store,
            oidc,
            oidc_provider=oidc_provider,
            allow_dev_login=allow_dev_login,
            default_org_id=default_org_id,
        )
        self.region_service = _maybe(RegionService)
        self.metrics_collector = _maybe(MetricsCollector)
        try:
            self.route_tuner = RouteTuner(self.metrics_collector, db_path)
        except TypeError:
            self.route_tuner = RouteTuner(self.metrics_collector, db_path)
        self.compliance_service = CompliancePackService(registry, db_path)
        self.context_graph = _maybe(ContextGraphService)
        self.discovery = _maybe(AgentDiscoveryService)
        self.message_bus = MessageBus(db_path=db_path, sql=self.sql)
        self.secrets = SecretsManager(
            db_path=db_path, master_key=secrets_key, sql=self.sql
        )
        self.tool_sandbox = ToolSandbox(
            policy=SandboxPolicy(timeout_seconds=sandbox_timeout_seconds),
            secrets=self.secrets,
        )
        self.tool_host = SandboxedToolHost(sandbox=self.tool_sandbox, secrets=self.secrets)
        self.tool_governor = ToolGovernor.from_config(
            redis_url=redis_url, backend=governor_backend
        )
        from ai_platform.knowledge.service import KnowledgeService
        from ai_platform.memory.service import MemoryService

        self.memory_service = MemoryService.durable(db_path=db_path, sql=self.sql)
        self.knowledge_service = KnowledgeService.durable(db_path=db_path, sql=self.sql)
        self.agent_engine = AgentEngine(
            tool_host=self.tool_host,
            governor=self.tool_governor,
            memory_service=self.memory_service,
            knowledge_service=self.knowledge_service,
            metrics_collector=self.metrics_collector,
        )
        self.eval_runner = EvaluationRunner(model_router=self.agent_engine.model_router)
        self.publish_service = PublishService(registry, self.policy_engine, self.eval_runner)
        self.readiness = ProductionReadinessEngine()
        self.workflow_engine = WorkflowEngine(
            agent_engine=self.agent_engine,
            tool_host=self.tool_host,
            governor=self.tool_governor,
            db_path=db_path,
            sql=self.sql,
        )
        try:
            mode = planner_mode if planner_mode in {"auto", "llm", "heuristic"} else "auto"
            self.dynamic_workflows = DynamicWorkflowEngine(
                db_path,
                workflow_engine=self.workflow_engine,
                agent_engine=self.agent_engine,
                sql=self.sql,
                model_router=self.agent_engine.model_router,
                planner_mode=mode,  # type: ignore[arg-type]
            )
        except TypeError:
            self.dynamic_workflows = DynamicWorkflowEngine(
                db_path,
                workflow_engine=self.workflow_engine,
                agent_engine=self.agent_engine,
            )
        self.federation_registry = FederationRegistry()
        self.federation = FederationGateway(
            local_domain=federation_domain,
            message_bus=self.message_bus,
            registry=self.federation_registry,
        )
        self.amtp = AMTPGateway(
            domain=federation_domain,
            message_bus=self.message_bus,
            sql=self.sql,
        )
