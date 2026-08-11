"""Shared resource models and types."""

from ai_platform.core.models import (
    AgentSpec,
    AuditEvent,
    BundleManifest,
    ExecutionEvent,
    ExecutionRequest,
    ModelRouteSpec,
    PlatformEvent,
    PlatformResource,
    PromptSpec,
    ResourceKind,
    ResourceMetadata,
    ResourceStatus,
    ToolSpec,
    ToolboxSpec,
)
from ai_platform.core.ids import new_id

__all__ = [
    "AgentSpec",
    "AuditEvent",
    "BundleManifest",
    "ExecutionEvent",
    "ExecutionRequest",
    "ModelRouteSpec",
    "PlatformEvent",
    "PlatformResource",
    "PromptSpec",
    "ResourceKind",
    "ResourceMetadata",
    "ResourceStatus",
    "ToolSpec",
    "ToolboxSpec",
    "new_id",
]
