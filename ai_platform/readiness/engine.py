"""Score whether an agent is safe to put (or keep) in production.

This is the intelligence layer over registry, evals, metrics, policy, and
bundles — not another editor. Missing evidence lowers the score on purpose.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

KIND_FROM_REF = {
    "agents": "Agent",
    "models": "ModelRoute",
    "prompts": "Prompt",
    "toolboxes": "Toolbox",
    "tools": "Tool",
    "guardrails": "Guardrail",
    "policies": "Policy",
    "evaluationsuites": "EvaluationSuite",
    "memory": "MemoryProfile",
    "knowledge": "KnowledgeSource",
    "workflows": "Workflow",
    "environments": "Environment",
}

WRITE_PERMS = frozenset(
    {
        "write",
        "delete",
        "execute",
        "*",
        "admin",
        "refund",
        "issue",
        "mutate",
        "create",
        "update",
    }
)

DIM_WEIGHTS = {
    "security": 0.22,
    "quality": 0.18,
    "reliability": 0.14,
    "governance": 0.14,
    "deployment": 0.12,
    "performance": 0.10,
    "cost": 0.10,
}


class ReadinessCheck(BaseModel):
    id: str
    dimension: str
    status: Literal["pass", "warn", "fail"]
    score: int
    title: str
    detail: str
    blocking: bool = False
    evidence: dict[str, Any] = Field(default_factory=dict)


class DimensionScore(BaseModel):
    name: str
    score: int
    status: Literal["pass", "warn", "fail"]
    checks: list[ReadinessCheck] = Field(default_factory=list)


class ReadinessReport(BaseModel):
    agent_ref: str = Field(alias="agentRef")
    version: str | None = None
    overall: int
    decision: Literal["safe_to_deploy", "watch", "not_ready"]
    decision_label: str = Field(alias="decisionLabel")
    dimensions: list[DimensionScore] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)
    previous_overall: int | None = Field(default=None, alias="previousOverall")
    drift: dict[str, Any] | None = None

    model_config = {"populate_by_name": True}


def _bundle_doc(bundle: dict[str, dict], ref: str | None) -> dict[str, Any] | None:
    if not ref:
        return None
    parts = str(ref).split("/", 1)
    if len(parts) != 2:
        return None
    kind = KIND_FROM_REF.get(parts[0])
    if not kind:
        return None
    return bundle.get(f"{kind}:{parts[1]}")


def _spec(doc: dict[str, Any] | None) -> dict[str, Any]:
    if not doc:
        return {}
    spec = doc.get("spec") or {}
    return spec if isinstance(spec, dict) else {}


def _check(
    *,
    cid: str,
    dimension: str,
    status: Literal["pass", "warn", "fail"],
    score: int,
    title: str,
    detail: str,
    blocking: bool = False,
    evidence: dict[str, Any] | None = None,
) -> ReadinessCheck:
    return ReadinessCheck(
        id=cid,
        dimension=dimension,
        status=status,
        score=max(0, min(100, score)),
        title=title,
        detail=detail,
        blocking=blocking,
        evidence=evidence or {},
    )


def _dim_status(score: int, has_fail: bool) -> Literal["pass", "warn", "fail"]:
    if has_fail or score < 60:
        return "fail"
    if score < 80:
        return "warn"
    return "pass"


class ProductionReadinessEngine:
    """Inspect a published agent graph and emit a production decision."""

    def __init__(self) -> None:
        self._last: dict[str, ReadinessReport] = {}

    def assess(
        self,
        *,
        agent_ref: str,
        bundle: dict[str, dict],
        version: str | None = None,
        eval_runs: list[dict[str, Any]] | None = None,
        route_metrics: dict[str, Any] | None = None,
        environments: list[dict[str, Any]] | None = None,
        published: bool = True,
        bundle_hash: str | None = None,
        has_publish_audit: bool = False,
        auth_required: bool = True,
        dev_login_enabled: bool = True,
    ) -> ReadinessReport:
        name = agent_ref.split("/", 1)[-1]
        agent_doc = bundle.get(f"Agent:{name}")
        spec = _spec(agent_doc)
        if not agent_doc:
            report = ReadinessReport(
                agent_ref=agent_ref,
                version=version,
                overall=0,
                decision="not_ready",
                decision_label="NOT READY FOR PRODUCTION",
                blockers=[f"Agent not in published bundle: {agent_ref}"],
                recommendations=["Publish the agent before running a production check."],
            )
            return report

        checks: list[ReadinessCheck] = []
        checks.extend(self._security(spec, bundle, auth_required, dev_login_enabled))
        checks.extend(self._reliability(spec, bundle, route_metrics))
        checks.extend(self._quality(agent_ref, bundle, eval_runs or []))
        checks.extend(self._cost(spec, bundle, eval_runs or [], route_metrics))
        checks.extend(self._performance(spec, bundle, eval_runs or [], route_metrics))
        checks.extend(self._governance(spec, bundle, environments or [], has_publish_audit))
        checks.extend(self._deployment(spec, bundle, published, bundle_hash, environments or []))

        by_dim: dict[str, list[ReadinessCheck]] = {k: [] for k in DIM_WEIGHTS}
        for c in checks:
            by_dim.setdefault(c.dimension, []).append(c)

        dimensions: list[DimensionScore] = []
        weighted = 0.0
        weight_sum = 0.0
        for name_dim, weight in DIM_WEIGHTS.items():
            dim_checks = by_dim.get(name_dim) or []
            if dim_checks:
                score = round(sum(c.score for c in dim_checks) / len(dim_checks))
            else:
                score = 40
            has_fail = any(c.status == "fail" for c in dim_checks)
            dimensions.append(
                DimensionScore(
                    name=name_dim,
                    score=score,
                    status=_dim_status(score, has_fail),
                    checks=dim_checks,
                )
            )
            weighted += score * weight
            weight_sum += weight

        overall = round(weighted / weight_sum) if weight_sum else 0
        blockers = [c.detail for c in checks if c.blocking]
        warnings = [c.detail for c in checks if c.status == "warn"]
        recs = self._recommendations(checks)

        if blockers or overall < 70:
            decision: Literal["safe_to_deploy", "watch", "not_ready"] = "not_ready"
            label = "NOT READY FOR PRODUCTION"
        elif overall < 80:
            decision = "watch"
            label = "WATCH — deploy with limits"
        else:
            decision = "safe_to_deploy"
            label = "SAFE TO DEPLOY"

        previous = self._last.get(agent_ref)
        drift = None
        if previous:
            delta = overall - previous.overall
            dim_prev = {d.name: d.score for d in previous.dimensions}
            dim_now = {d.name: d.score for d in dimensions}
            changed = {
                k: {"from": dim_prev[k], "to": dim_now[k], "delta": dim_now[k] - dim_prev[k]}
                for k in dim_now
                if k in dim_prev and dim_now[k] != dim_prev[k]
            }
            drift = {
                "previousOverall": previous.overall,
                "delta": delta,
                "degraded": delta <= -8,
                "dimensions": changed,
            }
            if drift["degraded"] and decision == "safe_to_deploy":
                decision = "watch"
                label = "DEGRADED — review before replacing production"
                recs.insert(0, f"Score dropped {previous.overall} → {overall}. Investigate changed dimensions.")

        report = ReadinessReport(
            agent_ref=agent_ref,
            version=version,
            overall=overall,
            decision=decision,
            decision_label=label,
            dimensions=dimensions,
            blockers=blockers,
            warnings=warnings,
            recommendations=recs,
            previous_overall=previous.overall if previous else None,
            drift=drift,
        )
        self._last[agent_ref] = report
        return report

    def assess_inventory(
        self,
        bundle: dict[str, dict],
        *,
        versions: dict[str, str] | None = None,
        eval_runs: list[dict[str, Any]] | None = None,
        metrics_by_route: dict[str, dict[str, Any]] | None = None,
        environments: list[dict[str, Any]] | None = None,
        hashes: dict[str, str | None] | None = None,
        has_publish_audit: dict[str, bool] | None = None,
        auth_required: bool = True,
        dev_login_enabled: bool = True,
    ) -> list[ReadinessReport]:
        out: list[ReadinessReport] = []
        for key, doc in bundle.items():
            if not key.startswith("Agent:"):
                continue
            name = doc.get("name") or key.split(":", 1)[-1]
            ref = f"agents/{name}"
            spec = _spec(doc)
            model_ref = str(spec.get("modelRef") or spec.get("model_ref") or "")
            route = model_ref.split("/", 1)[-1] if model_ref else ""
            out.append(
                self.assess(
                    agent_ref=ref,
                    bundle=bundle,
                    version=(versions or {}).get(name),
                    eval_runs=eval_runs,
                    route_metrics=(metrics_by_route or {}).get(route),
                    environments=environments,
                    published=True,
                    bundle_hash=(hashes or {}).get(name),
                    has_publish_audit=(has_publish_audit or {}).get(ref, False),
                    auth_required=auth_required,
                    dev_login_enabled=dev_login_enabled,
                )
            )
        out.sort(key=lambda r: (r.decision != "not_ready", r.overall))
        return out

    def _security(
        self,
        spec: dict[str, Any],
        bundle: dict[str, dict],
        auth_required: bool,
        dev_login_enabled: bool,
    ) -> list[ReadinessCheck]:
        checks: list[ReadinessCheck] = []
        g_refs = list(spec.get("guardrails") or [])
        types: set[str] = set()
        injection_blocks = False
        for ref in g_refs:
            gspec = _spec(_bundle_doc(bundle, ref))
            gtype = str(gspec.get("type") or "")
            types.add(gtype)
            cfg = gspec.get("config") if isinstance(gspec.get("config"), dict) else {}
            if gtype == "injection_detect" and str(cfg.get("action") or "").lower() == "block":
                injection_blocks = True

        if injection_blocks:
            checks.append(
                _check(
                    cid="sec.injection_block",
                    dimension="security",
                    status="pass",
                    score=100,
                    title="Prompt injection blocked",
                    detail="injection_detect guardrail is configured with action=block.",
                    evidence={"guardrails": g_refs},
                )
            )
        elif "injection_detect" in types:
            checks.append(
                _check(
                    cid="sec.injection_block",
                    dimension="security",
                    status="fail",
                    score=35,
                    title="Injection detect is not blocking",
                    detail="injection_detect exists but config.action is not block — injections only alert.",
                    blocking=True,
                    evidence={"guardrails": g_refs},
                )
            )
        else:
            checks.append(
                _check(
                    cid="sec.injection_block",
                    dimension="security",
                    status="fail",
                    score=20,
                    title="No prompt-injection protection",
                    detail="Attach guardrails/injection-detect with config.action=block before production.",
                    blocking=True,
                )
            )

        if "pii_mask" in types:
            checks.append(
                _check(
                    cid="sec.pii",
                    dimension="security",
                    status="pass",
                    score=100,
                    title="PII masking enabled",
                    detail="pii_mask guardrail is attached to this agent.",
                )
            )
        else:
            checks.append(
                _check(
                    cid="sec.pii",
                    dimension="security",
                    status="warn",
                    score=55,
                    title="No PII masking",
                    detail="No pii_mask guardrail. Customer data in prompts/tools may leak into logs and models.",
                )
            )

        policy_refs = list(spec.get("policies") or [])
        global_policies = [k for k in bundle if k.startswith("Policy:")]
        if policy_refs or global_policies:
            checks.append(
                _check(
                    cid="sec.policy",
                    dimension="security",
                    status="pass",
                    score=90 if policy_refs else 75,
                    title="Policy attached",
                    detail=(
                        f"Agent-scoped policies: {policy_refs}."
                        if policy_refs
                        else f"Namespace has {len(global_policies)} published Policy resource(s)."
                    ),
                    evidence={"agentPolicies": policy_refs, "globalPolicies": global_policies},
                )
            )
        else:
            checks.append(
                _check(
                    cid="sec.policy",
                    dimension="security",
                    status="fail",
                    score=30,
                    title="No policy coverage",
                    detail="No Policy resources and no agent.spec.policies. Runtime is not fail-closed for this agent.",
                    blocking=True,
                )
            )

        toolbox_ref = spec.get("toolboxRef") or spec.get("toolbox_ref")
        tb = _spec(_bundle_doc(bundle, toolbox_ref))
        tools = tb.get("tools") if isinstance(tb.get("tools"), list) else []
        if toolbox_ref and not tools:
            checks.append(
                _check(
                    cid="sec.least_privilege",
                    dimension="security",
                    status="fail",
                    score=25,
                    title="Toolbox missing from bundle",
                    detail=f"{toolbox_ref} is referenced but not published.",
                    blocking=True,
                )
            )
        elif tools:
            unconstrained = []
            write_without_approval = []
            for entry in tools:
                if not isinstance(entry, dict):
                    continue
                perms = [str(p).lower() for p in (entry.get("permissions") or [])]
                ref = str(entry.get("ref") or "?")
                if not perms or "*" in perms or "admin" in perms:
                    unconstrained.append(ref)
                if any(p in WRITE_PERMS for p in perms) and not entry.get("requireApproval") and not entry.get("require_approval"):
                    write_without_approval.append(ref)
            if unconstrained:
                checks.append(
                    _check(
                        cid="sec.least_privilege",
                        dimension="security",
                        status="fail",
                        score=25,
                        title="Tools lack least privilege",
                        detail=f"{len(unconstrained)} tool(s) have empty or wildcard permissions: {unconstrained[:5]}.",
                        blocking=True,
                        evidence={"tools": unconstrained},
                    )
                )
            elif write_without_approval:
                checks.append(
                    _check(
                        cid="sec.least_privilege",
                        dimension="security",
                        status="warn",
                        score=60,
                        title="Write tools skip HITL",
                        detail=f"Write/execute tools without requireApproval: {write_without_approval[:5]}.",
                        evidence={"tools": write_without_approval},
                    )
                )
            else:
                checks.append(
                    _check(
                        cid="sec.least_privilege",
                        dimension="security",
                        status="pass",
                        score=100,
                        title="Toolbox least privilege",
                        detail=f"{len(tools)} tool(s) declare permissions; write paths require approval.",
                    )
                )
        else:
            checks.append(
                _check(
                    cid="sec.least_privilege",
                    dimension="security",
                    status="pass",
                    score=85,
                    title="No toolbox",
                    detail="Agent has no toolbox — smaller blast radius, but confirm it does not invoke tools another way.",
                )
            )

        mcp_untrusted = []
        for entry in tools:
            if not isinstance(entry, dict):
                continue
            tdoc = _bundle_doc(bundle, entry.get("ref"))
            tspec = _spec(tdoc)
            if tspec.get("adapter") == "mcp":
                cfg = tspec.get("config") if isinstance(tspec.get("config"), dict) else {}
                if not cfg.get("command") and not cfg.get("url") and not cfg.get("server"):
                    mcp_untrusted.append(entry.get("ref"))
        if mcp_untrusted:
            checks.append(
                _check(
                    cid="sec.mcp",
                    dimension="security",
                    status="warn",
                    score=50,
                    title="MCP server not pinned",
                    detail=f"MCP tools without command/url/server: {mcp_untrusted}. Treat as untrusted.",
                )
            )
        else:
            checks.append(
                _check(
                    cid="sec.mcp",
                    dimension="security",
                    status="pass",
                    score=90,
                    title="MCP bindings pinned or unused",
                    detail="No unbound MCP adapters on this agent's toolbox.",
                )
            )

        if not auth_required:
            checks.append(
                _check(
                    cid="sec.auth",
                    dimension="security",
                    status="fail",
                    score=10,
                    title="Platform auth disabled",
                    detail="PLATFORM_AUTH_REQUIRED is false. Anyone can execute this agent.",
                    blocking=True,
                )
            )
        elif dev_login_enabled:
            checks.append(
                _check(
                    cid="sec.auth",
                    dimension="security",
                    status="warn",
                    score=65,
                    title="Dev email login enabled",
                    detail="Use OIDC (Okta/Azure AD) and disable PLATFORM_ALLOW_DEV_LOGIN for production.",
                )
            )
        else:
            checks.append(
                _check(
                    cid="sec.auth",
                    dimension="security",
                    status="pass",
                    score=100,
                    title="Auth required, dev login off",
                    detail="Control plane requires Bearer auth; email login is disabled.",
                )
            )
        return checks

    def _reliability(
        self,
        spec: dict[str, Any],
        bundle: dict[str, dict],
        route_metrics: dict[str, Any] | None,
    ) -> list[ReadinessCheck]:
        checks: list[ReadinessCheck] = []
        model_ref = spec.get("modelRef") or spec.get("model_ref")
        route = _spec(_bundle_doc(bundle, model_ref))
        candidates = route.get("candidates") if isinstance(route.get("candidates"), list) else []
        has_fallback = any(
            isinstance(c, dict) and c.get("fallback") for c in candidates
        ) or len(candidates) > 1
        if not model_ref or not route:
            checks.append(
                _check(
                    cid="rel.fallback",
                    dimension="reliability",
                    status="fail",
                    score=20,
                    title="Model route missing",
                    detail=f"modelRef {model_ref!r} is not published.",
                    blocking=True,
                )
            )
        elif has_fallback:
            checks.append(
                _check(
                    cid="rel.fallback",
                    dimension="reliability",
                    status="pass",
                    score=100,
                    title="Model fallback configured",
                    detail=f"{len(candidates)} candidate(s); at least one fallback path exists.",
                )
            )
        else:
            checks.append(
                _check(
                    cid="rel.fallback",
                    dimension="reliability",
                    status="fail",
                    score=40,
                    title="Single model, no fallback",
                    detail="Model timeout or provider outage will fail the agent. Add a fallback candidate.",
                    blocking=True,
                )
            )

        overview = (route_metrics or {}).get("overview") if isinstance(route_metrics, dict) else None
        requests = int((overview or {}).get("requests") or 0)
        success_rate = float((overview or {}).get("successRate") or 0)
        if requests < 5:
            checks.append(
                _check(
                    cid="rel.success_rate",
                    dimension="reliability",
                    status="warn",
                    score=50,
                    title="Insufficient production samples",
                    detail="Fewer than 5 model-route samples. Agent-level SLO is unknown — score capped.",
                    evidence={"requests": requests},
                )
            )
        elif success_rate < 0.95:
            checks.append(
                _check(
                    cid="rel.success_rate",
                    dimension="reliability",
                    status="fail",
                    score=40,
                    title="Model success rate below 95%",
                    detail=f"Route successRate={success_rate:.2%} over {requests} samples.",
                    blocking=success_rate < 0.85,
                    evidence={"successRate": success_rate, "requests": requests},
                )
            )
        else:
            checks.append(
                _check(
                    cid="rel.success_rate",
                    dimension="reliability",
                    status="pass",
                    score=100,
                    title="Model route healthy",
                    detail=f"successRate={success_rate:.2%} over {requests} samples.",
                )
            )

        collab = spec.get("collaboration") if isinstance(spec.get("collaboration"), dict) else {}
        max_iter = collab.get("maxIterations") or collab.get("max_iterations")
        if collab and (not max_iter or int(max_iter) > 8):
            checks.append(
                _check(
                    cid="rel.loop",
                    dimension="reliability",
                    status="warn",
                    score=55,
                    title="Unbounded or high collaboration loop",
                    detail="Set collaboration.maxIterations ≤ 8 so the agent cannot loop forever.",
                )
            )
        else:
            checks.append(
                _check(
                    cid="rel.loop",
                    dimension="reliability",
                    status="pass",
                    score=90,
                    title="Loop bound present",
                    detail="Single-agent or collaboration.maxIterations is bounded.",
                )
            )

        prompt_ref = spec.get("promptRef") or spec.get("prompt_ref")
        if _bundle_doc(bundle, prompt_ref):
            checks.append(
                _check(
                    cid="rel.prompt",
                    dimension="reliability",
                    status="pass",
                    score=100,
                    title="Prompt published",
                    detail=f"{prompt_ref} is in the bundle.",
                )
            )
        else:
            checks.append(
                _check(
                    cid="rel.prompt",
                    dimension="reliability",
                    status="fail",
                    score=20,
                    title="Prompt missing",
                    detail=f"promptRef {prompt_ref!r} is not published.",
                    blocking=True,
                )
            )
        return checks

    def _quality(
        self,
        agent_ref: str,
        bundle: dict[str, dict],
        eval_runs: list[dict[str, Any]],
    ) -> list[ReadinessCheck]:
        checks: list[ReadinessCheck] = []
        suites: list[tuple[str, dict[str, Any]]] = []
        for key, doc in bundle.items():
            if not key.startswith("EvaluationSuite:"):
                continue
            spec = _spec(doc)
            triggers = spec.get("triggers") or []
            matched = False
            for t in triggers:
                if not isinstance(t, dict):
                    continue
                on_pub = t.get("onPublish") or t.get("on_publish") or []
                if isinstance(on_pub, str):
                    on_pub = [on_pub]
                if agent_ref in on_pub or "*" in on_pub:
                    matched = True
            if matched:
                suites.append((key, spec))

        if not suites:
            checks.append(
                _check(
                    cid="qual.suite",
                    dimension="quality",
                    status="fail",
                    score=15,
                    title="No evaluation suite",
                    detail=f"No EvaluationSuite trigger includes {agent_ref} (or *). Cannot prove quality.",
                    blocking=True,
                )
            )
            dataset_n = 0
        else:
            dataset_n = max(len(s[1].get("dataset") or []) for s in suites)
            checks.append(
                _check(
                    cid="qual.suite",
                    dimension="quality",
                    status="pass",
                    score=100,
                    title="Evaluation suite linked",
                    detail=f"{len(suites)} suite(s) trigger on publish for this agent.",
                )
            )

        if dataset_n >= 8:
            checks.append(
                _check(
                    cid="qual.coverage",
                    dimension="quality",
                    status="pass",
                    score=100,
                    title="Golden set coverage",
                    detail=f"{dataset_n} cases in the largest linked suite.",
                )
            )
        elif dataset_n >= 3:
            checks.append(
                _check(
                    cid="qual.coverage",
                    dimension="quality",
                    status="warn",
                    score=55,
                    title="Thin golden set",
                    detail=f"Only {dataset_n} eval cases. Target ≥ 8 golden scenarios for production.",
                )
            )
        elif dataset_n > 0:
            checks.append(
                _check(
                    cid="qual.coverage",
                    dimension="quality",
                    status="fail",
                    score=30,
                    title="Eval coverage too low",
                    detail=f"Only {dataset_n} golden case(s). Insufficient to catch trajectory regressions.",
                    blocking=True,
                )
            )
        else:
            checks.append(
                _check(
                    cid="qual.coverage",
                    dimension="quality",
                    status="fail",
                    score=10,
                    title="No golden cases",
                    detail="Linked suite has an empty dataset (or no suite).",
                    blocking=True,
                )
            )

        related = [
            r
            for r in eval_runs
            if str(r.get("targetRef") or "") == agent_ref
        ]
        last = related[-1] if related else None
        if last is None:
            checks.append(
                _check(
                    cid="qual.last_run",
                    dimension="quality",
                    status="warn",
                    score=45,
                    title="No eval run recorded",
                    detail="Eval history is in-memory. Run a suite (or publish) so quality is evidenced.",
                )
            )
        elif not last.get("passed", True):
            checks.append(
                _check(
                    cid="qual.last_run",
                    dimension="quality",
                    status="fail",
                    score=25,
                    title="Last evaluation failed",
                    detail=str(last.get("gateReason") or "Suite gates did not pass."),
                    blocking=True,
                    evidence={"overall": last.get("overall"), "scores": last.get("scores")},
                )
            )
        else:
            overall = float(last.get("overall") or 0)
            checks.append(
                _check(
                    cid="qual.last_run",
                    dimension="quality",
                    status="pass" if overall >= 0.7 else "warn",
                    score=int(round(overall * 100)),
                    title="Last evaluation passed",
                    detail=f"overall={overall:.2f} on {last.get('runId')}.",
                    evidence={"scores": last.get("scores")},
                )
            )

        eval_types: set[str] = set()
        for _, s in suites:
            for ev in s.get("evaluators") or []:
                if isinstance(ev, dict) and ev.get("type"):
                    eval_types.add(str(ev["type"]))
        if "tool_accuracy" in eval_types or "faithfulness" in eval_types:
            checks.append(
                _check(
                    cid="qual.trajectory",
                    dimension="quality",
                    status="pass",
                    score=90,
                    title="Tool / groundedness judges present",
                    detail=f"Evaluators: {sorted(eval_types)}.",
                )
            )
        else:
            checks.append(
                _check(
                    cid="qual.trajectory",
                    dimension="quality",
                    status="warn",
                    score=50,
                    title="No trajectory-aware judges",
                    detail="Add tool_accuracy and faithfulness evaluators — final-answer judges miss dangerous tool paths.",
                    evidence={"evaluators": sorted(eval_types)},
                )
            )
        return checks

    def _cost(
        self,
        spec: dict[str, Any],
        bundle: dict[str, dict],
        eval_runs: list[dict[str, Any]],
        route_metrics: dict[str, Any] | None,
    ) -> list[ReadinessCheck]:
        checks: list[ReadinessCheck] = []
        route = _spec(_bundle_doc(bundle, spec.get("modelRef") or spec.get("model_ref")))
        strategy = str(route.get("strategy") or "")
        if strategy == "costOptimized":
            checks.append(
                _check(
                    cid="cost.strategy",
                    dimension="cost",
                    status="pass",
                    score=100,
                    title="Cost-optimized routing",
                    detail="ModelRoute strategy is costOptimized.",
                )
            )
        else:
            checks.append(
                _check(
                    cid="cost.strategy",
                    dimension="cost",
                    status="warn",
                    score=70,
                    title="Routing is not cost-optimized",
                    detail=f"strategy={strategy or 'unset'}. Worst-case spend is unbounded vs a cheaper candidate.",
                )
            )

        has_cost_judge = False
        for r in eval_runs:
            scores = r.get("scores") if isinstance(r.get("scores"), dict) else {}
            if "cost" in scores:
                has_cost_judge = True
        for key, doc in bundle.items():
            if not key.startswith("EvaluationSuite:"):
                continue
            for ev in _spec(doc).get("evaluators") or []:
                if isinstance(ev, dict) and ev.get("type") == "cost":
                    has_cost_judge = True
        if has_cost_judge:
            checks.append(
                _check(
                    cid="cost.gate",
                    dimension="cost",
                    status="pass",
                    score=95,
                    title="Cost eval gate present",
                    detail="A cost evaluator / score exists so regressions can fail the gate.",
                )
            )
        else:
            checks.append(
                _check(
                    cid="cost.gate",
                    dimension="cost",
                    status="warn",
                    score=50,
                    title="No cost gate",
                    detail="Add an evaluator type=cost with maxPerRun so a cheaper-but-worse model cannot silently ship.",
                )
            )

        overview = (route_metrics or {}).get("overview") if isinstance(route_metrics, dict) else {}
        requests = int(overview.get("requests") or 0)
        total_cost = float(overview.get("totalCostUnits") or 0)
        if requests < 5:
            checks.append(
                _check(
                    cid="cost.observed",
                    dimension="cost",
                    status="warn",
                    score=50,
                    title="Observed cost unknown",
                    detail="Not enough route samples to estimate p95/worst-case cost (token heuristic only).",
                )
            )
        else:
            avg = total_cost / requests
            checks.append(
                _check(
                    cid="cost.observed",
                    dimension="cost",
                    status="pass",
                    score=80,
                    title="Observed cost sampled",
                    detail=f"avg {avg:.4f} cost units/run over {requests} samples (heuristic, not billing).",
                    evidence={"avgCostUnits": avg, "totalCostUnits": total_cost},
                )
            )
        return checks

    def _performance(
        self,
        spec: dict[str, Any],
        bundle: dict[str, dict],
        eval_runs: list[dict[str, Any]],
        route_metrics: dict[str, Any] | None,
    ) -> list[ReadinessCheck]:
        checks: list[ReadinessCheck] = []
        route = _spec(_bundle_doc(bundle, spec.get("modelRef") or spec.get("model_ref")))
        candidates = route.get("candidates") if isinstance(route.get("candidates"), list) else []
        has_latency_cap = any(
            isinstance(c, dict) and (c.get("maxLatencyMs") or c.get("max_latency_ms"))
            for c in candidates
        )
        if has_latency_cap:
            checks.append(
                _check(
                    cid="perf.cap",
                    dimension="performance",
                    status="pass",
                    score=100,
                    title="Candidate latency cap",
                    detail="At least one model candidate sets maxLatencyMs.",
                )
            )
        else:
            checks.append(
                _check(
                    cid="perf.cap",
                    dimension="performance",
                    status="warn",
                    score=55,
                    title="No model timeout budget",
                    detail="Candidates have no maxLatencyMs. Timeouts will hang the agent.",
                )
            )

        overview = (route_metrics or {}).get("overview") if isinstance(route_metrics, dict) else {}
        requests = int(overview.get("requests") or 0)
        p95 = float(overview.get("p95LatencyMs") or 0)
        if requests < 5:
            checks.append(
                _check(
                    cid="perf.p95",
                    dimension="performance",
                    status="warn",
                    score=50,
                    title="Latency SLO unknown",
                    detail="Model-call metrics only; tool+retrieval e2e latency is not recorded yet.",
                )
            )
        elif p95 > 5000:
            checks.append(
                _check(
                    cid="perf.p95",
                    dimension="performance",
                    status="fail",
                    score=35,
                    title="p95 latency > 5s",
                    detail=f"Route p95={p95:.0f}ms over {requests} samples.",
                    evidence={"p95LatencyMs": p95},
                )
            )
        else:
            checks.append(
                _check(
                    cid="perf.p95",
                    dimension="performance",
                    status="pass",
                    score=95,
                    title="p95 latency in budget",
                    detail=f"p50={overview.get('p50LatencyMs')}ms p95={p95:.0f}ms.",
                )
            )

        has_lat_judge = any(
            isinstance(ev, dict) and ev.get("type") == "latency"
            for key, doc in bundle.items()
            if key.startswith("EvaluationSuite:")
            for ev in (_spec(doc).get("evaluators") or [])
        )
        if has_lat_judge:
            checks.append(
                _check(
                    cid="perf.gate",
                    dimension="performance",
                    status="pass",
                    score=90,
                    title="Latency eval gate",
                    detail="A latency evaluator can fail publish when p95 regresses.",
                )
            )
        else:
            checks.append(
                _check(
                    cid="perf.gate",
                    dimension="performance",
                    status="warn",
                    score=55,
                    title="No latency eval gate",
                    detail="Add evaluator type=latency so a slower model cannot replace production unnoticed.",
                )
            )
        return checks

    def _governance(
        self,
        spec: dict[str, Any],
        bundle: dict[str, dict],
        environments: list[dict[str, Any]],
        has_publish_audit: bool,
    ) -> list[ReadinessCheck]:
        checks: list[ReadinessCheck] = []
        env_specs: list[dict[str, Any]] = []
        for key, doc in bundle.items():
            if key.startswith("Environment:"):
                env_specs.append({**_spec(doc), "name": doc.get("name")})
        env_specs.extend(environments)

        require_approval = any(
            e.get("requireApproval") or e.get("require_approval") for e in env_specs
        )
        approvers = []
        for e in env_specs:
            approvers.extend(e.get("approvers") or [])
        if require_approval and approvers:
            checks.append(
                _check(
                    cid="gov.approval",
                    dimension="governance",
                    status="pass",
                    score=100,
                    title="Production approval required",
                    detail=f"Environment requireApproval with {len(approvers)} approver(s).",
                )
            )
        elif require_approval:
            checks.append(
                _check(
                    cid="gov.approval",
                    dimension="governance",
                    status="warn",
                    score=60,
                    title="Approval required but no approvers",
                    detail="Set Environment.spec.approvers so promotions are not stuck / self-approved.",
                )
            )
        else:
            checks.append(
                _check(
                    cid="gov.approval",
                    dimension="governance",
                    status="fail",
                    score=35,
                    title="No production approver",
                    detail="No Environment with requireApproval. Anyone who can publish can ship to prod.",
                    blocking=True,
                )
            )

        if has_publish_audit:
            checks.append(
                _check(
                    cid="gov.audit",
                    dimension="governance",
                    status="pass",
                    score=100,
                    title="Publish audit trail",
                    detail="At least one resource.published audit event exists for this agent.",
                )
            )
        else:
            checks.append(
                _check(
                    cid="gov.audit",
                    dimension="governance",
                    status="warn",
                    score=55,
                    title="No publish audit yet",
                    detail="No audit event for this agent. Publish (or re-publish) so the trail exists.",
                )
            )

        signed = any(
            str(e.get("bundlePolicy") or e.get("bundle_policy") or "").lower() in {"signed", "signed-only"}
            for e in env_specs
        )
        if signed:
            checks.append(
                _check(
                    cid="gov.signed",
                    dimension="governance",
                    status="pass",
                    score=100,
                    title="Signed-only bundle policy",
                    detail="Production environment requires signed bundles.",
                )
            )
        else:
            checks.append(
                _check(
                    cid="gov.signed",
                    dimension="governance",
                    status="warn",
                    score=60,
                    title="Unsigned bundle policy",
                    detail="Set Environment.bundlePolicy=signed-only so unsigned graphs cannot run in prod.",
                )
            )

        owner = (spec.get("owner") if isinstance(spec.get("owner"), str) else None) or None
        # labels live on metadata, not spec — unknown here unless annotations stuffed in spec
        if owner:
            checks.append(
                _check(
                    cid="gov.owner",
                    dimension="governance",
                    status="pass",
                    score=90,
                    title="Owner declared",
                    detail=f"owner={owner}",
                )
            )
        else:
            checks.append(
                _check(
                    cid="gov.owner",
                    dimension="governance",
                    status="warn",
                    score=50,
                    title="No owner",
                    detail="Add metadata.labels.owner (or spec.owner) so this is not a shadow agent.",
                )
            )
        return checks

    def _deployment(
        self,
        spec: dict[str, Any],
        bundle: dict[str, dict],
        published: bool,
        bundle_hash: str | None,
        environments: list[dict[str, Any]],
    ) -> list[ReadinessCheck]:
        checks: list[ReadinessCheck] = []
        if published:
            checks.append(
                _check(
                    cid="dep.published",
                    dimension="deployment",
                    status="pass",
                    score=100,
                    title="Published version",
                    detail="A published pointer exists for this agent.",
                )
            )
        else:
            checks.append(
                _check(
                    cid="dep.published",
                    dimension="deployment",
                    status="fail",
                    score=0,
                    title="Not published",
                    detail="Only drafts exist — nothing to deploy.",
                    blocking=True,
                )
            )

        if bundle_hash:
            checks.append(
                _check(
                    cid="dep.hash",
                    dimension="deployment",
                    status="pass",
                    score=100,
                    title="Signed bundle hash",
                    detail=f"bundleHash={bundle_hash[:16]}…",
                )
            )
        else:
            checks.append(
                _check(
                    cid="dep.hash",
                    dimension="deployment",
                    status="warn",
                    score=50,
                    title="No bundle hash",
                    detail="Publish did not record a bundle hash — rollback identity is weak.",
                )
            )

        has_prod = any(
            str(doc.get("name") or key.split(":", 1)[-1]).lower() in {"production", "prod"}
            for key, doc in bundle.items()
            if key.startswith("Environment:")
        ) or any(
            str(e.get("name") or "").lower() in {"production", "prod"} for e in environments
        )
        if has_prod:
            checks.append(
                _check(
                    cid="dep.prod_env",
                    dimension="deployment",
                    status="pass",
                    score=95,
                    title="Production environment CRD",
                    detail="An Environment named production/prod is published.",
                )
            )
        else:
            checks.append(
                _check(
                    cid="dep.prod_env",
                    dimension="deployment",
                    status="warn",
                    score=45,
                    title="No production Environment",
                    detail="Publish an Environment CRD with promotionFrom + requireApproval.",
                )
            )

        # Rollback: another published agent version is not stored on the pointer —
        # presence of latest != published would need registry. Unknown → warn.
        checks.append(
            _check(
                cid="dep.rollback",
                dimension="deployment",
                status="warn",
                score=55,
                title="Rollback not proven",
                detail="Platform keeps a single published pointer. Keep the previous version published in staging for rollback.",
            )
        )
        return checks

    def _recommendations(self, checks: list[ReadinessCheck]) -> list[str]:
        recs: list[str] = []
        for c in checks:
            if c.status == "fail":
                recs.append(c.detail)
        for c in checks:
            if c.status == "warn" and len(recs) < 8:
                recs.append(c.detail)
        return recs[:8]
