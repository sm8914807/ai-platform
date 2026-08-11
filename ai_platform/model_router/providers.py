"""Real model provider adapters — OpenAI, Anthropic, Bedrock (+ mock)."""

from __future__ import annotations

import json
import os
import time
from typing import Any

import httpx

from ai_platform.model_router.router import ModelProvider, ModelRequest, ModelResponse


class OpenAIProvider(ModelProvider):
    name = "openai"

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str = "https://api.openai.com/v1",
    ) -> None:
        self.api_key = api_key or os.getenv("OPENAI_API_KEY", "")
        self.base_url = base_url.rstrip("/")

    async def complete(self, model: str, request: ModelRequest) -> ModelResponse:
        if not self.api_key:
            raise RuntimeError("OPENAI_API_KEY not configured")
        start = time.perf_counter()
        body: dict[str, Any] = {
            "model": model,
            "messages": request.messages,
            "temperature": request.temperature,
        }
        if request.max_tokens:
            body["max_tokens"] = request.max_tokens
        if request.tools:
            body["tools"] = request.tools

        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{self.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json=body,
            )
            resp.raise_for_status()
            data = resp.json()

        choice = data["choices"][0]["message"]
        usage = data.get("usage", {})
        return ModelResponse(
            content=choice.get("content") or "",
            provider=self.name,
            model=model,
            usage={
                "prompt_tokens": usage.get("prompt_tokens", 0),
                "completion_tokens": usage.get("completion_tokens", 0),
                "total_tokens": usage.get("total_tokens", 0),
            },
            latency_ms=(time.perf_counter() - start) * 1000,
        )


class AnthropicProvider(ModelProvider):
    name = "anthropic"

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str = "https://api.anthropic.com",
        api_version: str = "2023-06-01",
    ) -> None:
        self.api_key = api_key or os.getenv("ANTHROPIC_API_KEY", "")
        self.base_url = base_url.rstrip("/")
        self.api_version = api_version

    async def complete(self, model: str, request: ModelRequest) -> ModelResponse:
        if not self.api_key:
            raise RuntimeError("ANTHROPIC_API_KEY not configured")
        start = time.perf_counter()

        system_parts = [
            m["content"] for m in request.messages if m.get("role") == "system"
        ]
        messages = [
            {"role": m["role"], "content": m["content"]}
            for m in request.messages
            if m.get("role") in ("user", "assistant")
        ]
        # Anthropic requires alternating user/assistant; ensure starts with user
        if not messages:
            messages = [{"role": "user", "content": ""}]

        body: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": request.max_tokens or 4096,
            "temperature": request.temperature,
        }
        if system_parts:
            body["system"] = "\n\n".join(str(s) for s in system_parts)

        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{self.base_url}/v1/messages",
                headers={
                    "x-api-key": self.api_key,
                    "anthropic-version": self.api_version,
                    "Content-Type": "application/json",
                },
                json=body,
            )
            resp.raise_for_status()
            data = resp.json()

        content_blocks = data.get("content", [])
        text = "".join(
            b.get("text", "") for b in content_blocks if b.get("type") == "text"
        )
        usage = data.get("usage", {})
        return ModelResponse(
            content=text,
            provider=self.name,
            model=model,
            usage={
                "prompt_tokens": usage.get("input_tokens", 0),
                "completion_tokens": usage.get("output_tokens", 0),
                "total_tokens": usage.get("input_tokens", 0) + usage.get("output_tokens", 0),
            },
            latency_ms=(time.perf_counter() - start) * 1000,
        )


class BedrockProvider(ModelProvider):
    """AWS Bedrock via Converse API (HTTP + SigV4 optional; uses bearer token or local mock path).

    For production, set BEDROCK_API_KEY or use AWS credentials via boto3 if installed.
    Falls back to invoke-model style HTTP when BEDROCK_ENDPOINT is set (for LocalStack/proxy).
    """

    name = "bedrock"

    def __init__(
        self,
        region: str | None = None,
        api_key: str | None = None,
        endpoint: str | None = None,
    ) -> None:
        self.region = region or os.getenv("AWS_REGION", "us-east-1")
        self.api_key = api_key or os.getenv("BEDROCK_API_KEY", "")
        self.endpoint = (
            endpoint
            or os.getenv("BEDROCK_ENDPOINT")
            or f"https://bedrock-runtime.{self.region}.amazonaws.com"
        )

    async def complete(self, model: str, request: ModelRequest) -> ModelResponse:
        start = time.perf_counter()

        # Prefer boto3 if available and no custom endpoint override for LocalStack-style
        if not os.getenv("BEDROCK_ENDPOINT"):
            try:
                return await self._complete_boto3(model, request, start)
            except ImportError:
                pass
            except Exception:
                # Fall through to HTTP if boto3 path fails without credentials in tests
                if not self.api_key and not os.getenv("AWS_ACCESS_KEY_ID"):
                    raise RuntimeError(
                        "Bedrock requires boto3+AWS credentials or BEDROCK_API_KEY/BEDROCK_ENDPOINT"
                    )

        return await self._complete_http(model, request, start)

    async def _complete_boto3(
        self, model: str, request: ModelRequest, start: float
    ) -> ModelResponse:
        import asyncio

        import boto3

        def _invoke() -> dict[str, Any]:
            client = boto3.client("bedrock-runtime", region_name=self.region)
            system = [
                {"text": m["content"]}
                for m in request.messages
                if m.get("role") == "system"
            ]
            messages = []
            for m in request.messages:
                if m.get("role") in ("user", "assistant"):
                    messages.append(
                        {
                            "role": m["role"],
                            "content": [{"text": str(m["content"])}],
                        }
                    )
            kwargs: dict[str, Any] = {
                "modelId": model,
                "messages": messages,
                "inferenceConfig": {
                    "temperature": request.temperature,
                    "maxTokens": request.max_tokens or 4096,
                },
            }
            if system:
                kwargs["system"] = system
            return client.converse(**kwargs)

        data = await asyncio.to_thread(_invoke)
        text = ""
        for block in data.get("output", {}).get("message", {}).get("content", []):
            if "text" in block:
                text += block["text"]
        usage = data.get("usage", {})
        return ModelResponse(
            content=text,
            provider=self.name,
            model=model,
            usage={
                "prompt_tokens": usage.get("inputTokens", 0),
                "completion_tokens": usage.get("outputTokens", 0),
                "total_tokens": usage.get("totalTokens", 0),
            },
            latency_ms=(time.perf_counter() - start) * 1000,
        )

    async def _complete_http(
        self, model: str, request: ModelRequest, start: float
    ) -> ModelResponse:
        """HTTP Converse for proxies / API-key gateways."""
        messages = []
        system: list[dict[str, str]] = []
        for m in request.messages:
            if m.get("role") == "system":
                system.append({"text": str(m["content"])})
            elif m.get("role") in ("user", "assistant"):
                messages.append(
                    {"role": m["role"], "content": [{"text": str(m["content"])}]}
                )
        body: dict[str, Any] = {
            "messages": messages,
            "inferenceConfig": {
                "temperature": request.temperature,
                "maxTokens": request.max_tokens or 4096,
            },
        }
        if system:
            body["system"] = system

        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{self.endpoint.rstrip('/')}/model/{model}/converse",
                headers=headers,
                json=body,
            )
            resp.raise_for_status()
            data = resp.json()

        text = ""
        for block in data.get("output", {}).get("message", {}).get("content", []):
            if "text" in block:
                text += block["text"]
        usage = data.get("usage", {})
        return ModelResponse(
            content=text,
            provider=self.name,
            model=model,
            usage={
                "prompt_tokens": usage.get("inputTokens", 0),
                "completion_tokens": usage.get("outputTokens", 0),
                "total_tokens": usage.get("totalTokens", 0),
            },
            latency_ms=(time.perf_counter() - start) * 1000,
        )


def build_default_providers() -> dict[str, ModelProvider]:
    """Register mock always; real providers when keys/env present."""
    from ai_platform.model_router.router import MockModelProvider

    providers: dict[str, ModelProvider] = {"mock": MockModelProvider()}
    providers["openai"] = OpenAIProvider()
    providers["anthropic"] = AnthropicProvider()
    providers["bedrock"] = BedrockProvider()
    # Azure OpenAI compatible via openai base URL
    azure_key = os.getenv("AZURE_OPENAI_API_KEY")
    azure_base = os.getenv("AZURE_OPENAI_ENDPOINT")
    if azure_key and azure_base:
        providers["azure"] = OpenAIProvider(
            api_key=azure_key,
            base_url=azure_base.rstrip("/") + "/openai/deployments",
        )
    return providers
