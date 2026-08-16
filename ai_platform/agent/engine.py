"""Single-agent execution engine."""

from typing import Any, AsyncIterator

from ai_platform.core.ids import new_id
from ai_platform.core.models import (
    AgentSpec,
    ExecutionEvent,
    MemoryProfileSpec,
    ModelRouteSpec,
    ToolboxEntry,
    ToolSpec,
    ToolboxSpec,
)
from ai_platform.governor.engine import ToolGovernor
from ai_platform.context.engineer import ContextBudget, ContextEngineer
from ai_platform.guardrails.pipeline import GuardrailPipeline
from ai_platform.knowledge.service import KnowledgeService
from ai_platform.memory.service import MemoryService
from ai_platform.model_router.providers import build_default_providers
from ai_platform.model_router.router import ModelRouter, ModelRequest
from ai_platform.tool_host.host import ToolHost
from ai_platform.tool_host.sandbox import SandboxedToolHost


class AgentEngine:
    """Executes a single agent from resolved bundle resources."""

    def __init__(
        self,
        model_router: ModelRouter | None = None,
        tool_host: ToolHost | None = None,
        memory_service: MemoryService | None = None,
        knowledge_service: KnowledgeService | None = None,
        guardrail_pipeline: GuardrailPipeline | None = None,
        context_engineer: ContextEngineer | None = None,
        governor: ToolGovernor | None = None,
        metrics_collector: Any | None = None,
    ) -> None:
        self.model_router = model_router or ModelRouter(
            providers=build_default_providers(),
            metrics_collector=metrics_collector,
        )
        if metrics_collector is not None and getattr(self.model_router, "_metrics", None) is None:
            self.model_router._metrics = metrics_collector
        self.tool_host = tool_host or SandboxedToolHost()
        self.memory_service = memory_service or MemoryService()
        self.knowledge_service = knowledge_service or KnowledgeService()
        self.guardrail_pipeline = guardrail_pipeline or GuardrailPipeline()
        self.context_engineer = context_engineer or ContextEngineer()
        self.governor = governor or ToolGovernor(fail_closed=False)
        self.metrics_collector = metrics_collector

    def _resolve(self, bundle: dict[str, dict], ref: str) -> dict[str, Any] | None:
        parts = ref.split("/", 1)
        if len(parts) != 2:
            return None
        plural, name = parts
        kind_map = {
            "agents": "Agent",
            "prompts": "Prompt",
            "tools": "Tool",
            "toolboxes": "Toolbox",
            "models": "ModelRoute",
            "memory": "MemoryProfile",
            "knowledge": "KnowledgeSource",
            "guardrails": "Guardrail",
        }
        kind = kind_map.get(plural)
        if not kind:
            return None
        return bundle.get(f"{kind}:{name}")

    async def execute(
        self,
        bundle_resources: dict[str, dict],
        agent_ref: str,
        input_data: dict[str, Any],
        stream: bool = False,
        session_id: str | None = None,
        org_id: str = "default",
        namespace_id: str = "local",
    ) -> AsyncIterator[ExecutionEvent] | ExecutionEvent:
        execution_id = new_id("exec")
        agent_doc = self._resolve(bundle_resources, agent_ref)
        if not agent_doc:
            event = ExecutionEvent(
                type="error",
                data={"message": f"Agent not found: {agent_ref}"},
                execution_id=execution_id,
            )
            if stream:
                async def _err():
                    yield event
                return _err()
            return event

        spec = AgentSpec.model_validate(agent_doc["spec"])
        prompt_doc = self._resolve(bundle_resources, spec.prompt_ref)
        model_doc = self._resolve(bundle_resources, spec.model_ref)

        memory_profile: MemoryProfileSpec | None = None
        scope = session_id or execution_id
        if spec.memory_ref:
            mem_doc = self._resolve(bundle_resources, spec.memory_ref)
            if mem_doc:
                memory_profile = MemoryProfileSpec.model_validate(mem_doc["spec"])

        user_content = str(input_data.get("message", input_data))
        guardrail_specs = self.guardrail_pipeline.load_from_bundle(
            bundle_resources, spec.guardrails
        )
        user_content, alerts = await self.guardrail_pipeline.run_input(
            user_content, guardrail_specs
        )

        retrieval_context = ""
        if spec.knowledge_refs:
            chunks = await self.knowledge_service.retrieve_for_agent(
                user_content, spec.knowledge_refs, bundle_resources
            )
            if chunks:
                retrieval_context = self.knowledge_service.store.format_citations(chunks)

        history_messages: list[dict[str, str]] = []
        if memory_profile:
            entries = await self.memory_service.read(scope, memory_profile)
            history_messages = self.memory_service.conversation_messages(entries)

        prompt_template = prompt_doc["spec"]["template"] if prompt_doc else "You are a helpful assistant."
        prompt_text = prompt_template.replace("{{ input }}", user_content)
        if retrieval_context:
            prompt_text = f"Context:\n{retrieval_context}\n\n{prompt_text}"

        raw_messages = history_messages + [{"role": "user", "content": prompt_text}]

        # Context engineering — token budget, relevance filter, summarization
        if memory_profile:
            for layer in memory_profile.layers:
                if layer.max_tokens:
                    self.context_engineer.budget = ContextBudget(max_tokens=layer.max_tokens)
                    break
        ctx_result = self.context_engineer.prepare(raw_messages, query=user_content)
        messages = ctx_result.messages

        route_spec = (
            ModelRouteSpec.model_validate(model_doc["spec"])
            if model_doc
            else ModelRouteSpec(
                candidates=[{"provider": "mock", "model": "mock-1", "weight": 100}]
            )
        )

        bound_tools: list[tuple[ToolboxEntry, ToolSpec]] = []
        if spec.toolbox_ref:
            toolbox_doc = self._resolve(bundle_resources, spec.toolbox_ref)
            if toolbox_doc:
                toolbox = ToolboxSpec.model_validate(toolbox_doc["spec"])
                for entry in toolbox.tools:
                    tool_doc = self._resolve(bundle_resources, entry.ref)
                    if tool_doc:
                        bound_tools.append(
                            (entry, ToolSpec.model_validate(tool_doc["spec"]))
                        )

        async def _stream() -> AsyncIterator[ExecutionEvent]:
            if alerts:
                yield ExecutionEvent(
                    type="token",
                    data={"text": "", "guardrailAlerts": alerts},
                    execution_id=execution_id,
                )

            if bound_tools and input_data.get("use_tool"):
                entry, tool_spec = bound_tools[0]
                quota = entry.rate_limit or tool_spec.rate_limit
                if not input_data.get("governor_override"):
                    decision = await self.governor.check(
                        tool_ref=entry.ref,
                        rate_limit=quota,
                        org_id=org_id,
                        namespace_id=namespace_id,
                    )
                    if not decision.allowed:
                        payload = self.governor.approval_payload(
                            decision,
                            tool_name=tool_spec.manifest.name,
                            tool_ref=entry.ref,
                        )
                        payload["agentRef"] = agent_ref
                        yield ExecutionEvent(
                            type="approval_required",
                            data=payload,
                            execution_id=execution_id,
                        )
                        return
                yield ExecutionEvent(
                    type="tool_call",
                    data={"tool": tool_spec.manifest.name, "input": input_data},
                    execution_id=execution_id,
                )
                result = await self.tool_host.invoke(tool_spec, input_data)
                yield ExecutionEvent(
                    type="tool_result",
                    data={"output": result.output, "latencyMs": result.latency_ms},
                    execution_id=execution_id,
                )

            response = await self.model_router.complete(
                route_spec,
                ModelRequest(messages=messages),
                route_name=spec.model_ref,
                namespace_id=namespace_id,
            )
            output_text, out_alerts = await self.guardrail_pipeline.run_output(
                response.content, guardrail_specs
            )

            for chunk in output_text.split(" "):
                yield ExecutionEvent(
                    type="token",
                    data={"text": chunk + " "},
                    execution_id=execution_id,
                )

            await self.memory_service.write(
                scope,
                {"role": "user", "content": user_content},
                memory_profile,
            )
            await self.memory_service.write(
                scope,
                {"role": "assistant", "content": output_text},
                memory_profile,
            )

            yield ExecutionEvent(
                type="done",
                data={
                    "content": output_text,
                    "provider": response.provider,
                    "model": response.model,
                    "usage": response.usage,
                    "guardrailAlerts": out_alerts,
                    "retrievalChunks": len(retrieval_context.split("\n")) if retrieval_context else 0,
                    "contextEngineering": {
                        "originalTokens": ctx_result.original_tokens,
                        "finalTokens": ctx_result.final_tokens,
                        "summarized": ctx_result.summarized,
                        "filtered": ctx_result.filtered,
                        "notes": ctx_result.notes,
                    },
                },
                execution_id=execution_id,
            )

        if stream:
            return _stream()

        events: list[ExecutionEvent] = []
        async for ev in _stream():
            events.append(ev)
        return events[-1] if events else ExecutionEvent(type="error", data={}, execution_id=execution_id)
