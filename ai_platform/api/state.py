"""Application state."""

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
from ai_platform.marketplace.service import MarketplaceCatalog, MarketplaceService
from ai_platform.messaging.bus import MessageBus
from ai_platform.model_router.tuner import RouteTuner
from ai_platform.observability.metrics import MetricsCollector
from ai_platform.policy.engine import PolicyEngine
from ai_platform.promotion.service import PromotionService
from ai_platform.publish.service import PublishService
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

        try:
            self.workflow_engine = WorkflowEngine(db_path=db_path, sql=self.sql)
        except TypeError:
            self.workflow_engine = WorkflowEngine(db_path=db_path)

        self.marketplace_catalog = _maybe(MarketplaceCatalog)
        self.marketplace_service = MarketplaceService(self.marketplace_catalog, registry)
        try:
            self.git_sync = GitSyncService(registry, db_path)
        except TypeError:
            self.git_sync = GitSyncService(registry, db_path)
        self.identity_store = _maybe(IdentityStore)
        self.scim_service = ScimService(self.identity_store)
        oidc = OidcValidator(secret="dev-platform-secret-change-in-prod")
        self.sso_service = SsoService(self.identity_store, oidc)
        self.region_service = _maybe(RegionService)
        self.metrics_collector = _maybe(MetricsCollector)
        try:
            self.route_tuner = RouteTuner(self.metrics_collector, db_path)
        except TypeError:
            self.route_tuner = RouteTuner(self.metrics_collector, db_path)
        self.compliance_service = CompliancePackService(registry, db_path)
        self.context_graph = _maybe(ContextGraphService)
        self.discovery = _maybe(AgentDiscoveryService)
        try:
            self.dynamic_workflows = DynamicWorkflowEngine(
                db_path, workflow_engine=self.workflow_engine, sql=self.sql
            )
        except TypeError:
            self.dynamic_workflows = DynamicWorkflowEngine(
                db_path, workflow_engine=self.workflow_engine
            )
        self.message_bus = MessageBus(db_path=db_path, sql=self.sql)
        self.secrets = SecretsManager(
            db_path=db_path, master_key=secrets_key, sql=self.sql
        )
        self.tool_sandbox = ToolSandbox(
            policy=SandboxPolicy(timeout_seconds=sandbox_timeout_seconds),
            secrets=self.secrets,
        )
        self.tool_host = SandboxedToolHost(sandbox=self.tool_sandbox, secrets=self.secrets)
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
