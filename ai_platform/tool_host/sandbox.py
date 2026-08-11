"""Sandboxed tool execution — allowlists, timeouts, secret injection."""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

from ai_platform.core.models import ToolSpec
from ai_platform.secrets.manager import SecretsManager
from ai_platform.tool_host.host import ToolAdapter, ToolHost, ToolResult


@dataclass
class SandboxPolicy:
    allowed_hosts: list[str] = field(default_factory=list)
    blocked_hosts: list[str] = field(
        default_factory=lambda: ["169.254.169.254", "metadata.google.internal"]
    )
    allow_network: bool = True
    allow_subprocess: bool = False
    timeout_seconds: float = 30.0
    max_output_bytes: int = 256_000


class SandboxViolation(Exception):
    pass


class ToolSandbox:
    def __init__(
        self,
        policy: SandboxPolicy | None = None,
        secrets: SecretsManager | None = None,
    ) -> None:
        self.policy = policy or SandboxPolicy()
        self.secrets = secrets

    def check_url(self, url: str) -> None:
        if not self.policy.allow_network:
            raise SandboxViolation("network disabled by sandbox policy")
        host = urlparse(url).hostname or ""
        if host in self.policy.blocked_hosts:
            raise SandboxViolation(f"blocked host: {host}")
        if self.policy.allowed_hosts and host not in self.policy.allowed_hosts:
            if not any(host == h or host.endswith("." + h) for h in self.policy.allowed_hosts):
                raise SandboxViolation(f"host not allowlisted: {host}")

    async def resolve_secrets(
        self, namespace_id: str | None, config: dict[str, Any]
    ) -> dict[str, Any]:
        if not self.secrets or not namespace_id:
            return config
        out = dict(config)
        secret_ref = out.get("secretRef") or out.get("authRef")
        if isinstance(secret_ref, str) and secret_ref.startswith("secrets/"):
            name = secret_ref.split("/", 1)[1]
            value = await self.secrets.get(namespace_id, name)
            if value is not None:
                out["resolvedSecret"] = value
                if value.startswith("{"):
                    try:
                        out["auth"] = json.loads(value)
                    except json.JSONDecodeError:
                        out["apiKey"] = value
                else:
                    out["apiKey"] = value
        return out


class SandboxedRestAdapter(ToolAdapter):
    def __init__(self, sandbox: ToolSandbox) -> None:
        self.sandbox = sandbox

    async def invoke(self, spec: ToolSpec, input_data: dict[str, Any]) -> ToolResult:
        start = time.perf_counter()
        config = dict(spec.config)
        namespace_id = None
        payload = dict(input_data)
        if "_namespace_id" in payload:
            namespace_id = payload.pop("_namespace_id")
        config = await self.sandbox.resolve_secrets(namespace_id, config)

        url = config.get("url", "")
        method = config.get("method", "GET").upper()
        mock = config.get("mock", url == "" or config.get("dryRun", False))

        if url and not mock:
            self.sandbox.check_url(url)
            import httpx

            headers = dict(config.get("headers") or {})
            if config.get("apiKey"):
                headers.setdefault("Authorization", f"Bearer {config['apiKey']}")
            async with httpx.AsyncClient(timeout=self.sandbox.policy.timeout_seconds) as client:
                resp = await client.request(method, url, json=payload or None, headers=headers)
                try:
                    body: Any = resp.json()
                except Exception:
                    body = resp.text[: self.sandbox.policy.max_output_bytes]
                output = {
                    "adapter": "rest",
                    "method": method,
                    "url": url,
                    "status": resp.status_code,
                    "body": body,
                    "sandbox": True,
                }
        else:
            output = {
                "adapter": "rest",
                "method": method,
                "url": url,
                "input": payload,
                "mock": True,
                "sandbox": True,
            }
        return ToolResult(output, (time.perf_counter() - start) * 1000)


class SandboxedToolHost(ToolHost):
    def __init__(
        self,
        sandbox: ToolSandbox | None = None,
        secrets: SecretsManager | None = None,
    ) -> None:
        super().__init__()
        self.sandbox = sandbox or ToolSandbox(secrets=secrets)
        self._adapters["rest"] = SandboxedRestAdapter(self.sandbox)

    async def invoke(
        self,
        spec: ToolSpec,
        input_data: dict[str, Any],
        namespace_id: str | None = None,
    ) -> ToolResult:
        data = dict(input_data)
        if namespace_id:
            data["_namespace_id"] = namespace_id
        try:
            return await asyncio.wait_for(
                super().invoke(spec, data),
                timeout=self.sandbox.policy.timeout_seconds,
            )
        except asyncio.TimeoutError as e:
            raise SandboxViolation("tool invocation timed out") from e
