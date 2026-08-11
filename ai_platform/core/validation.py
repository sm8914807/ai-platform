"""JSON Schema validation for platform resources."""

import json
from pathlib import Path
from typing import Any

import jsonschema
from jsonschema import Draft202012Validator

from ai_platform.core.models import PlatformResource, ResourceKind

SCHEMAS_DIR = Path(__file__).parent.parent.parent / "schemas" / "v1"

_KIND_TO_SCHEMA: dict[str, str] = {
    ResourceKind.AGENT.value: "agent.json",
    ResourceKind.PROMPT.value: "prompt.json",
    ResourceKind.TOOL.value: "tool.json",
    ResourceKind.TOOLBOX.value: "toolbox.json",
    ResourceKind.MODEL_ROUTE.value: "model-route.json",
    ResourceKind.WORKFLOW.value: "workflow.json",
    ResourceKind.MEMORY_PROFILE.value: "memory-profile.json",
    ResourceKind.KNOWLEDGE_SOURCE.value: "knowledge-source.json",
    ResourceKind.POLICY.value: "policy.json",
    ResourceKind.GUARDRAIL.value: "guardrail.json",
    ResourceKind.EVALUATION_SUITE.value: "evaluation-suite.json",
    ResourceKind.ENVIRONMENT.value: "environment.json",
    ResourceKind.PLUGIN.value: "plugin.json",
}


def _load_validator(kind: str) -> Draft202012Validator | None:
    filename = _KIND_TO_SCHEMA.get(kind)
    if not filename:
        return None
    path = SCHEMAS_DIR / filename
    if not path.exists():
        return None
    schema = json.loads(path.read_text())
    meta_path = SCHEMAS_DIR / "metadata.json"
    if meta_path.exists() and "metadata" in schema.get("properties", {}):
        meta = meta_path.read_text()
        if schema["properties"]["metadata"].get("$ref") == "metadata.json":
            schema["properties"]["metadata"] = json.loads(meta)
    return Draft202012Validator(schema)


def validate_resource_document(doc: dict[str, Any]) -> list[str]:
    """Return list of validation errors; empty if valid."""
    kind = doc.get("kind")
    if not kind:
        return ["missing kind"]
    validator = _load_validator(kind)
    if validator is None:
        return []
    errors = sorted(validator.iter_errors(doc), key=lambda e: e.path)
    return [e.message for e in errors]


def validate_platform_resource(resource: PlatformResource) -> list[str]:
    return validate_resource_document(resource.to_document())
