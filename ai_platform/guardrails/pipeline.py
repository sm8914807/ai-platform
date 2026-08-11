"""Guardrail plugins — PII masking, injection detection."""

import re
from abc import ABC, abstractmethod
from typing import Any

from ai_platform.core.models import GuardrailSpec


class GuardrailPlugin(ABC):
    @abstractmethod
    async def apply(self, text: str, config: dict[str, Any]) -> tuple[str, list[str]]:
        """Return (transformed_text, alerts)."""


class PIIMaskGuardrail(GuardrailPlugin):
    PATTERNS = {
        "email": re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+"),
        "phone": re.compile(r"\b\d{3}[-.]?\d{3}[-.]?\d{4}\b"),
        "credit_card": re.compile(r"\b(?:\d[ -]*?){13,16}\b"),
    }

    async def apply(self, text: str, config: dict[str, Any]) -> tuple[str, list[str]]:
        entities = config.get("entities", ["email", "phone", "credit_card"])
        alerts: list[str] = []
        out = text
        for entity in entities:
            pat = self.PATTERNS.get(entity)
            if pat and pat.search(out):
                alerts.append(f"pii_detected:{entity}")
                out = pat.sub(f"[{entity.upper()}_MASKED]", out)
        return out, alerts


class InjectionDetectGuardrail(GuardrailPlugin):
    INJECTION_PATTERNS = [
        re.compile(r"ignore\s+(all\s+)?previous\s+instructions", re.I),
        re.compile(r"system\s*:\s*", re.I),
        re.compile(r"<\s*script", re.I),
    ]

    async def apply(self, text: str, config: dict[str, Any]) -> tuple[str, list[str]]:
        alerts: list[str] = []
        action = config.get("action", "alert")
        for pat in self.INJECTION_PATTERNS:
            if pat.search(text):
                alerts.append("injection_pattern_detected")
                if action == "block":
                    return "", alerts
        return text, alerts


class GuardrailPipeline:
    def __init__(self) -> None:
        self._plugins: dict[str, GuardrailPlugin] = {
            "pii_mask": PIIMaskGuardrail(),
            "injection_detect": InjectionDetectGuardrail(),
        }

    def load_from_bundle(self, bundle: dict[str, dict], guardrail_refs: list[str]) -> list[GuardrailSpec]:
        specs: list[GuardrailSpec] = []
        for ref in guardrail_refs:
            parts = ref.split("/", 1)
            if len(parts) != 2:
                continue
            name = parts[1]
            doc = bundle.get(f"Guardrail:{name}")
            if doc:
                specs.append(GuardrailSpec.model_validate(doc["spec"]))
        return specs

    async def run_input(self, text: str, specs: list[GuardrailSpec]) -> tuple[str, list[str]]:
        alerts: list[str] = []
        out = text
        for spec in specs:
            plugin = self._plugins.get(spec.type)
            if plugin:
                out, a = await plugin.apply(out, spec.config)
                alerts.extend(a)
        return out, alerts

    async def run_output(self, text: str, specs: list[GuardrailSpec]) -> tuple[str, list[str]]:
        return await self.run_input(text, specs)
