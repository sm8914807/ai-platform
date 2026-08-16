"""MCP transports — stdio subprocess and Streamable HTTP."""

from __future__ import annotations

import asyncio
import json
import os
from abc import ABC, abstractmethod
from typing import Any


class McpTransportError(Exception):
    def __init__(self, message: str, *, code: int | None = None, data: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.data = data


class McpTransport(ABC):
    @abstractmethod
    async def request(self, method: str, params: dict[str, Any] | None = None) -> Any:
        ...

    @abstractmethod
    async def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        ...

    @abstractmethod
    async def close(self) -> None:
        ...


class StdioMcpTransport(McpTransport):
    """Newline-delimited JSON-RPC over a spawned MCP server process."""

    def __init__(
        self,
        command: str,
        args: list[str] | None = None,
        *,
        env: dict[str, str] | None = None,
        cwd: str | None = None,
        timeout_seconds: float = 30.0,
    ) -> None:
        self.command = command
        self.args = args or []
        self.env = env
        self.cwd = cwd
        self.timeout_seconds = timeout_seconds
        self._proc: asyncio.subprocess.Process | None = None
        self._id = 0
        self._lock = asyncio.Lock()

    async def _ensure_proc(self) -> asyncio.subprocess.Process:
        if self._proc and self._proc.returncode is None:
            return self._proc
        env = os.environ.copy()
        if self.env:
            env.update({str(k): str(v) for k, v in self.env.items()})
        self._proc = await asyncio.create_subprocess_exec(
            self.command,
            *self.args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=self.cwd,
            env=env,
            limit=8 * 1024 * 1024,
        )
        return self._proc

    async def _read_message(self, proc: asyncio.subprocess.Process) -> dict[str, Any]:
        assert proc.stdout is not None
        while True:
            line = await asyncio.wait_for(proc.stdout.readline(), timeout=self.timeout_seconds)
            if not line:
                stderr = ""
                if proc.stderr:
                    try:
                        err = await asyncio.wait_for(proc.stderr.read(4096), timeout=0.2)
                        stderr = err.decode(errors="replace")
                    except (asyncio.TimeoutError, Exception):
                        pass
                raise McpTransportError(
                    f"MCP stdio server closed unexpectedly"
                    + (f": {stderr.strip()}" if stderr.strip() else "")
                )
            text = line.decode().strip()
            if not text:
                continue
            try:
                msg = json.loads(text)
            except json.JSONDecodeError:
                # Skip non-JSON log lines that some servers emit on stdout.
                continue
            if isinstance(msg, dict):
                return msg

    async def request(self, method: str, params: dict[str, Any] | None = None) -> Any:
        async with self._lock:
            proc = await self._ensure_proc()
            assert proc.stdin is not None
            self._id += 1
            req_id = self._id
            payload: dict[str, Any] = {
                "jsonrpc": "2.0",
                "id": req_id,
                "method": method,
            }
            if params is not None:
                payload["params"] = params
            proc.stdin.write((json.dumps(payload) + "\n").encode())
            await proc.stdin.drain()

            while True:
                msg = await self._read_message(proc)
                if "id" not in msg:
                    # notification from server — ignore for request/response cycle
                    continue
                if msg.get("id") != req_id:
                    continue
                if "error" in msg:
                    err = msg["error"] or {}
                    raise McpTransportError(
                        str(err.get("message") or "MCP error"),
                        code=err.get("code"),
                        data=err.get("data"),
                    )
                return msg.get("result")

    async def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        async with self._lock:
            proc = await self._ensure_proc()
            assert proc.stdin is not None
            payload: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
            if params is not None:
                payload["params"] = params
            proc.stdin.write((json.dumps(payload) + "\n").encode())
            await proc.stdin.drain()

    async def close(self) -> None:
        proc = self._proc
        self._proc = None
        if not proc:
            return
        try:
            if proc.stdin:
                proc.stdin.close()
            try:
                await asyncio.wait_for(proc.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
        except ProcessLookupError:
            pass


class HttpMcpTransport(McpTransport):
    """Streamable HTTP MCP transport (JSON response or SSE event stream)."""

    def __init__(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        timeout_seconds: float = 30.0,
        protocol_version: str = "2025-03-26",
    ) -> None:
        self.url = url.rstrip("/")
        self.headers = dict(headers or {})
        self.timeout_seconds = timeout_seconds
        self.protocol_version = protocol_version
        self._session_id: str | None = None
        self._id = 0
        self._lock = asyncio.Lock()

    def _base_headers(self) -> dict[str, str]:
        h = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": self.protocol_version,
            **self.headers,
        }
        if self._session_id:
            h["Mcp-Session-Id"] = self._session_id
        return h

    async def _post(self, body: dict[str, Any]) -> Any:
        import httpx

        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            resp = await client.post(self.url, json=body, headers=self._base_headers())
            sid = resp.headers.get("mcp-session-id") or resp.headers.get("Mcp-Session-Id")
            if sid:
                self._session_id = sid
            if resp.status_code >= 400:
                raise McpTransportError(
                    f"MCP HTTP {resp.status_code}: {resp.text[:500]}",
                    code=resp.status_code,
                )
            ctype = (resp.headers.get("content-type") or "").lower()
            if "text/event-stream" in ctype:
                return self._parse_sse(resp.text)
            if not resp.content:
                return None
            try:
                data = resp.json()
            except Exception as e:
                raise McpTransportError(f"invalid MCP HTTP JSON: {resp.text[:300]}") from e
            return data

    def _parse_sse(self, text: str) -> Any:
        """Extract the last JSON-RPC message from an SSE body."""
        last: Any = None
        data_lines: list[str] = []
        for raw in text.splitlines():
            line = raw.rstrip("\r")
            if line.startswith("data:"):
                data_lines.append(line[5:].lstrip())
            elif line == "" and data_lines:
                blob = "\n".join(data_lines)
                data_lines = []
                try:
                    last = json.loads(blob)
                except json.JSONDecodeError:
                    continue
        if data_lines:
            try:
                last = json.loads("\n".join(data_lines))
            except json.JSONDecodeError:
                pass
        if last is None:
            raise McpTransportError("empty SSE response from MCP server")
        return last

    def _unwrap(self, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        if "error" in data:
            err = data["error"] or {}
            raise McpTransportError(
                str(err.get("message") or "MCP error"),
                code=err.get("code"),
                data=err.get("data"),
            )
        if "result" in data:
            return data.get("result")
        return data

    async def request(self, method: str, params: dict[str, Any] | None = None) -> Any:
        async with self._lock:
            self._id += 1
            body: dict[str, Any] = {
                "jsonrpc": "2.0",
                "id": self._id,
                "method": method,
            }
            if params is not None:
                body["params"] = params
            raw = await self._post(body)
            return self._unwrap(raw)

    async def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        async with self._lock:
            body: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
            if params is not None:
                body["params"] = params
            await self._post(body)

    async def close(self) -> None:
        self._session_id = None
