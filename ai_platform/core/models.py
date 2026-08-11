"""Platform CRD models (Pydantic)."""

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


class ResourceKind(str, Enum):
    AGENT = "Agent"
    WORKFLOW = "Workflow"
    PROMPT = "Prompt"
    TOOLBOX = "Toolbox"
    TOOL = "Tool"
    MODEL_ROUTE = "ModelRoute"
    MEMORY_PROFILE = "MemoryProfile"
    KNOWLEDGE_SOURCE = "KnowledgeSource"
    CONNECTOR = "Connector"
    GUARDRAIL = "Guardrail"
    POLICY = "Policy"
    EVALUATION_SUITE = "EvaluationSuite"
    MCP_BINDING = "MCPBinding"
    APPROVAL_FLOW = "ApprovalFlow"
    ENVIRONMENT = "Environment"
    DEPLOYMENT = "Deployment"
    PLUGIN = "Plugin"



class ResourceMetadata(BaseModel):
    name: str
    namespace: str
    version: str = "0.0.1"
    labels: dict[str, str] = Field(default_factory=dict)
    annotations: dict[str, str] = Field(default_factory=dict)


class ResourceStatus(BaseModel):
    published: bool = False
    bundle_hash: str | None = None


class PlatformResource(BaseModel):
    api_version: Literal["platform.ai/v1"] = "platform.ai/v1"
    kind: ResourceKind
    metadata: ResourceMetadata
    spec: dict[str, Any]
    status: ResourceStatus | None = None

    def to_document(self) -> dict[str, Any]:
        doc: dict[str, Any] = {
            "apiVersion": self.api_version,
            "kind": self.kind.value,
            "metadata": {
                "name": self.metadata.name,
                "namespace": self.metadata.namespace,
                "version": self.metadata.version,
                "labels": self.metadata.labels,
                "annotations": self.metadata.annotations,
            },
            "spec": self.spec,
        }
        if self.status:
            status: dict[str, Any] = {"published": self.status.published}
            if self.status.bundle_hash is not None:
                status["bundleHash"] = self.status.bundle_hash
            doc["status"] = status
        return doc


class AgentSpec(BaseModel):
    role: Literal["executor", "planner", "supervisor", "reviewer", "reflector", "router"]
    model_ref: str = Field(alias="modelRef")
    prompt_ref: str = Field(alias="promptRef")
    toolbox_ref: str | None = Field(default=None, alias="toolboxRef")
    memory_ref: str | None = Field(default=None, alias="memoryRef")
    knowledge_refs: list[str] = Field(default_factory=list, alias="knowledgeRefs")
    guardrails: list[str] = Field(default_factory=list)
    supervisor_ref: str | None = Field(default=None, alias="supervisorRef")
    policies: list[str] = Field(default_factory=list)

    model_config = {"populate_by_name": True}


class PromptSpec(BaseModel):
    template: str
    variables: dict[str, Any] = Field(default_factory=dict)


class ToolManifest(BaseModel):
    name: str
    description: str | None = None
    input_schema: dict[str, Any] = Field(default_factory=dict, alias="inputSchema")
    output_schema: dict[str, Any] = Field(default_factory=dict, alias="outputSchema")

    model_config = {"populate_by_name": True}


class ToolSpec(BaseModel):
    adapter: Literal["mcp", "openapi", "rest", "graphql", "grpc", "cli", "custom"]
    manifest: ToolManifest
    config: dict[str, Any] = Field(default_factory=dict)
    auth_ref: str | None = None


class ToolboxEntry(BaseModel):
    ref: str
    permissions: list[str] = Field(default_factory=list)
    rate_limit: str | None = None
    require_approval: bool = False


class ToolboxSpec(BaseModel):
    tools: list[ToolboxEntry]


class ModelCandidate(BaseModel):
    provider: str
    model: str
    weight: int = 100
    fallback: bool = False
    max_latency_ms: int | None = None


class ModelRouteSpec(BaseModel):
    strategy: Literal[
        "weightedFallback", "costOptimized", "latencyOptimized", "capabilityMatch"
    ] = "weightedFallback"
    candidates: list[ModelCandidate]
    constraints: dict[str, Any] = Field(default_factory=dict)
    caching: dict[str, Any] = Field(default_factory=dict)


class AuditEvent(BaseModel):
    id: str
    org_id: str
    actor_id: str | None = None
    action: str
    resource_ref: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    ip: str | None = None
    created_at: datetime


class PlatformEvent(BaseModel):
    event_id: str
    type: str
    timestamp: datetime
    org_id: str
    namespace: str
    data: dict[str, Any] = Field(default_factory=dict)
    trace_id: str | None = None


class ExecutionRequest(BaseModel):
    resource_ref: str
    input: dict[str, Any] = Field(default_factory=dict)
    session_id: str | None = None
    stream: bool = False
    trace_id: str | None = None


class ExecutionEvent(BaseModel):
    type: Literal["token", "tool_call", "tool_result", "approval_required", "done", "error"]
    data: dict[str, Any] = Field(default_factory=dict)
    execution_id: str | None = None


class BundleManifest(BaseModel):
    namespace: str
    environment: str
    bundle_hash: str
    signature: str
    resources: list[dict[str, Any]]
    created_at: datetime


# --- Phase 2 models ---


class MemoryLayer(BaseModel):
    type: Literal["conversation", "semantic", "entity", "session", "episodic"]
    backend: str
    ttl: str | None = None
    max_tokens: int | None = Field(default=None, alias="maxTokens")
    retention: str | None = None

    model_config = {"populate_by_name": True}


class MemoryProfileSpec(BaseModel):
    layers: list[MemoryLayer]
    summarization: dict[str, Any] = Field(default_factory=dict)
    versioning: bool = False


class KnowledgeSourceSpec(BaseModel):
    connector: dict[str, Any] = Field(default_factory=dict)
    ingestion: dict[str, Any] = Field(default_factory=dict)
    indexing: dict[str, Any] = Field(default_factory=dict)
    retrieval: dict[str, Any] = Field(default_factory=dict)
    citations: dict[str, Any] = Field(default_factory=dict)
    documents: list[dict[str, Any]] = Field(default_factory=list)


class PolicyRule(BaseModel):
    effect: Literal["allow", "deny"]
    principals: list[str] = Field(default_factory=list)
    actions: list[str]
    resources: list[str]
    conditions: dict[str, Any] = Field(default_factory=dict)


class PolicySpec(BaseModel):
    rules: list[PolicyRule]


class GuardrailSpec(BaseModel):
    type: Literal["pii_mask", "injection_detect", "content_moderation", "custom"]
    config: dict[str, Any] = Field(default_factory=dict)


class EvaluationSuiteSpec(BaseModel):
    dataset: list[dict[str, Any]] = Field(default_factory=list)
    evaluators: list[dict[str, Any]] = Field(default_factory=list)
    triggers: list[dict[str, Any]] = Field(default_factory=list)
    gates: dict[str, Any] = Field(default_factory=dict)


class EnvironmentSpec(BaseModel):
    promotion_from: str | None = Field(default=None, alias="promotionFrom")
    require_approval: bool = Field(default=False, alias="requireApproval")
    approvers: list[str] = Field(default_factory=list)
    bundle_policy: str | None = Field(default=None, alias="bundlePolicy")

    model_config = {"populate_by_name": True}


class WorkflowStep(BaseModel):
    id: str
    type: Literal["agent", "tool", "parallel", "humanApproval", "workflow"]
    ref: str | None = None
    when: str | None = None
    timeout: str | None = None
    retry: dict[str, Any] = Field(default_factory=dict)
    branches: list[dict[str, Any]] = Field(default_factory=list)


class WorkflowSpec(BaseModel):
    trigger: dict[str, Any] = Field(default_factory=dict)
    steps: list[WorkflowStep]


class PolicyContext(BaseModel):
    principal: str = "anonymous"
    action: str
    resource: str
    environment: str = "development"
    org_id: str = ""
    attributes: dict[str, Any] = Field(default_factory=dict)


class PolicyDecision(BaseModel):
    allowed: bool
    reason: str | None = None
    matched_rule: str | None = None


class RetrievalChunk(BaseModel):
    chunk_id: str
    source_id: str
    doc_id: str
    text: str
    score: float
    metadata: dict[str, Any] = Field(default_factory=dict)


class MemoryEntry(BaseModel):
    id: str
    scope: str
    layer: str
    content: dict[str, Any]
    version: int = 1
    created_at: datetime


class WorkflowRunState(BaseModel):
    run_id: str
    workflow_ref: str
    status: Literal["running", "paused", "completed", "failed", "waiting_approval"]
    steps: dict[str, Any] = Field(default_factory=dict)
    current_step_id: str | None = None
    input: dict[str, Any] = Field(default_factory=dict)
    output: dict[str, Any] = Field(default_factory=dict)
    checkpoint_seq: int = 0


# --- Phase 3 models ---


class CollaborationSpec(BaseModel):
    pattern: Literal[
        "planner_executor_reviewer",
        "hierarchical",
        "supervisor_workers",
        "peer_round_robin",
    ] = "planner_executor_reviewer"
    max_iterations: int = Field(default=3, alias="maxIterations")
    shared_context: bool = Field(default=True, alias="sharedContext")
    context_scope: Literal["session", "task", "org"] = Field(default="session", alias="contextScope")
    agents: dict[str, str] = Field(default_factory=dict)

    model_config = {"populate_by_name": True}


class PluginManifest(BaseModel):
    type: str
    version: str
    author: str | None = None
    tier: Literal["community", "verified", "enterprise"] = "community"
    permissions: list[str] = Field(default_factory=list)
    resources: list[dict[str, Any]] = Field(default_factory=list)
    pricing: dict[str, Any] = Field(default_factory=dict)


class IdentityUser(BaseModel):
    id: str
    org_id: str
    email: str
    display_name: str | None = None
    external_id: str | None = None
    teams: list[str] = Field(default_factory=list)
    active: bool = True


class ScimUserPayload(BaseModel):
    schemas: list[str] = Field(default_factory=lambda: ["urn:ietf:params:scim:schemas:core:2.0:User"])
    userName: str
    name: dict[str, str] = Field(default_factory=dict)
    emails: list[dict[str, str]] = Field(default_factory=list)
    active: bool = True
    externalId: str | None = None


class GitSyncResult(BaseModel):
    repo_id: str
    applied: int
    skipped: int
    errors: list[str] = Field(default_factory=list)
    commit: str | None = None


class MultiAgentResult(BaseModel):
    pattern: str
    iterations: int
    steps: list[dict[str, Any]] = Field(default_factory=list)
    final_output: dict[str, Any] = Field(default_factory=dict)


# --- Phase 4 models ---


class RegionConfig(BaseModel):
    id: str
    name: str
    endpoint: str
    data_residency: str | None = None
    is_primary: bool = False
    status: Literal["active", "standby", "offline"] = "active"


class EdgeRuntimeConfig(BaseModel):
    mode: Literal["embedded", "remote", "edge", "hybrid"] = "embedded"
    bundle_cache_path: str = ".platform/bundle.cache.json"
    telemetry_only: bool = False
    region: str | None = None
    sync_interval_seconds: int = 300


class CompliancePack(BaseModel):
    id: str
    name: str
    framework: Literal["HIPAA", "PCI", "GDPR", "SOC2", "ISO27001"]
    version: str
    description: str
    resources: list[dict[str, Any]] = Field(default_factory=list)


class ModelRouteMetric(BaseModel):
    route_name: str
    provider: str
    model: str
    latency_ms: float
    success: bool
    cost_units: float = 0.0


class RouteTuningResult(BaseModel):
    route_name: str
    old_weights: dict[str, int]
    new_weights: dict[str, int]
    reason: str
    metrics_window: int
