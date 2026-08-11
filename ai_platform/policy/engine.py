"""RBAC + ABAC policy evaluation."""

import fnmatch
from typing import Any

from ai_platform.core.models import PolicyContext, PolicyDecision, PolicySpec


class PolicyEngine:
    """Fail-closed policy gate."""

    def __init__(self, policies: list[PolicySpec] = None) -> None:
        self._policies = policies or []

    def load_from_bundle(self, bundle: dict[str, dict]) -> None:
        specs: list[PolicySpec] = []
        for key, doc in bundle.items():
            if key.startswith("Policy:"):
                specs.append(PolicySpec.model_validate(doc["spec"]))
        self._policies = specs

    def evaluate(self, ctx: PolicyContext) -> PolicyDecision:
        if not self._policies:
            return PolicyDecision(allowed=True, reason="no policies configured")

        allow_match = False
        deny_match = False
        matched: str | None = None

        for policy_idx, policy in enumerate(self._policies):
            for rule_idx, rule in enumerate(policy.rules):
                if not self._match_principal(rule.principals, ctx.principal):
                    continue
                if not self._match_action(rule.actions, ctx.action):
                    continue
                if not self._match_resource(rule.resources, ctx.resource):
                    continue
                if not self._match_conditions(rule.conditions, ctx):
                    continue
                matched = f"policy[{policy_idx}].rule[{rule_idx}]"
                if rule.effect == "deny":
                    deny_match = True
                else:
                    allow_match = True

        if deny_match:
            return PolicyDecision(allowed=False, reason="explicit deny", matched_rule=matched)
        if allow_match:
            return PolicyDecision(allowed=True, reason="explicit allow", matched_rule=matched)
        return PolicyDecision(allowed=False, reason="default deny (fail closed)")

    def _match_principal(self, principals: list[str], principal: str) -> bool:
        if not principals:
            return True
        for p in principals:
            if p == "*" or p == principal:
                return True
            if p.startswith("team:") and principal.startswith(p):
                return True
        return False

    def _match_action(self, actions: list[str], action: str) -> bool:
        return any(fnmatch.fnmatch(action, a) for a in actions)

    def _match_resource(self, resources: list[str], resource: str) -> bool:
        return any(fnmatch.fnmatch(resource, r) for r in resources)

    def _match_conditions(self, conditions: dict[str, Any], ctx: PolicyContext) -> bool:
        if not conditions:
            return True
        for key, expected in conditions.items():
            if key == "env" and ctx.environment != expected:
                return False
            if key in ctx.attributes and ctx.attributes[key] != expected:
                return False
        return True
