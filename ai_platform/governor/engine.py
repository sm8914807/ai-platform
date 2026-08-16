"""Tool-call quotas backed by in-memory counters or Redis Lua."""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass
from typing import Any, Protocol

from ai_platform.core.models import ToolboxSpec, ToolSpec

_UNITS: dict[str, int] = {
    "s": 1,
    "sec": 1,
    "second": 1,
    "seconds": 1,
    "m": 60,
    "min": 60,
    "minute": 60,
    "minutes": 60,
    "h": 3600,
    "hr": 3600,
    "hour": 3600,
    "hours": 3600,
    "d": 86400,
    "day": 86400,
    "days": 86400,
}

_RATE_RE = re.compile(r"^(\d+)\s*(?:/|per)\s*([a-z]+)$")

REDIS_FIXED_WINDOW = """
local n = redis.call('INCR', KEYS[1])
local window = tonumber(ARGV[2])
if n == 1 then
  redis.call('EXPIRE', KEYS[1], window)
end
local ttl = redis.call('TTL', KEYS[1])
if ttl < 0 then
  redis.call('EXPIRE', KEYS[1], window)
  ttl = window
end
local limit = tonumber(ARGV[1])
if n > limit then
  return {0, limit, 0, ttl}
end
return {1, limit, limit - n, ttl}
"""


@dataclass(frozen=True)
class RateLimit:
    count: int
    window_seconds: int
    raw: str


@dataclass(frozen=True)
class ConsumeResult:
    allowed: bool
    remaining: int
    reset_after_seconds: int


@dataclass(frozen=True)
class GovernorDecision:
    allowed: bool
    limit: int
    remaining: int
    reset_after_seconds: int
    key: str
    reason: str | None = None


class CounterStore(Protocol):
    async def consume(self, key: str, limit: int, window_seconds: int) -> ConsumeResult: ...


def parse_rate_limit(raw: str) -> RateLimit:
    text = raw.strip().lower().replace(" per ", "/")
    match = _RATE_RE.match(text)
    if not match:
        raise ValueError(f"invalid rate limit: {raw!r} (expected '20/min' or '3 per hour')")
    count = int(match.group(1))
    unit = match.group(2)
    window = _UNITS.get(unit)
    if window is None:
        raise ValueError(f"unknown rate limit unit: {unit!r}")
    if count < 1:
        raise ValueError(f"rate limit count must be >= 1: {raw!r}")
    return RateLimit(count=count, window_seconds=window, raw=raw.strip())


@dataclass
class _Bucket:
    count: int
    expires_at: float


class MemoryCounterStore:
    """Process-local fixed window. Used in tests and when Redis is not configured."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._buckets: dict[str, _Bucket] = {}

    async def consume(self, key: str, limit: int, window_seconds: int) -> ConsumeResult:
        async with self._lock:
            now = time.monotonic()
            bucket = self._buckets.get(key)
            if bucket is None or bucket.expires_at <= now:
                bucket = _Bucket(count=0, expires_at=now + window_seconds)
            bucket.count += 1
            self._buckets[key] = bucket
            remaining = max(0, limit - bucket.count)
            reset = max(0, int(bucket.expires_at - now))
            return ConsumeResult(
                allowed=bucket.count <= limit,
                remaining=remaining,
                reset_after_seconds=reset,
            )


class RedisCounterStore:
    """Atomic fixed-window counters via one Lua EVAL per check."""

    def __init__(self, url: str, client: Any | None = None) -> None:
        self._url = url
        self._client = client

    async def _redis(self) -> Any:
        if self._client is None:
            import redis.asyncio as redis

            self._client = redis.from_url(self._url, decode_responses=True)
        return self._client

    async def consume(self, key: str, limit: int, window_seconds: int) -> ConsumeResult:
        client = await self._redis()
        raw = await client.eval(REDIS_FIXED_WINDOW, 1, key, str(limit), str(window_seconds))
        allowed, _limit, remaining, ttl = (int(v) for v in raw)
        return ConsumeResult(
            allowed=bool(allowed),
            remaining=remaining,
            reset_after_seconds=max(0, ttl),
        )


def quota_for_tool(bundle: dict[str, dict], tool_ref: str) -> tuple[str | None, bool]:
    """Return (rate_limit, require_approval) from a toolbox entry, else the tool spec."""
    for doc in bundle.values():
        if doc.get("kind") != "Toolbox":
            continue
        spec = ToolboxSpec.model_validate(doc["spec"])
        for entry in spec.tools:
            if entry.ref == tool_ref:
                return entry.rate_limit, entry.require_approval
    parts = tool_ref.split("/", 1)
    if len(parts) == 2:
        tool_doc = bundle.get(f"Tool:{parts[1]}")
        if tool_doc:
            tool = ToolSpec.model_validate(tool_doc["spec"])
            return tool.rate_limit, False
    return None, False


class ToolGovernor:
    """Allow or trip a tool-call quota. Callers pause for approval on deny."""

    def __init__(
        self,
        store: CounterStore | None = None,
        fail_closed: bool = True,
        *,
        backend: str = "memory",
    ) -> None:
        self.store = store or MemoryCounterStore()
        self.fail_closed = fail_closed
        self.backend = backend

    @classmethod
    def from_redis_url(cls, url: str | None) -> ToolGovernor:
        return cls.from_config(redis_url=url, backend="auto")

    @classmethod
    def from_config(
        cls,
        *,
        redis_url: str | None = None,
        backend: str = "auto",
    ) -> ToolGovernor:
        choice = (backend or "auto").strip().lower()
        if choice not in {"auto", "memory", "redis"}:
            raise ValueError(
                f"invalid governor backend: {backend!r} (expected auto|memory|redis)"
            )
        if choice == "memory":
            return cls(MemoryCounterStore(), fail_closed=False, backend="memory")
        if choice == "redis":
            if not redis_url:
                raise ValueError(
                    "PLATFORM_GOVERNOR_BACKEND=redis requires PLATFORM_REDIS_URL"
                )
            return cls(RedisCounterStore(redis_url), fail_closed=True, backend="redis")
        if redis_url:
            return cls(RedisCounterStore(redis_url), fail_closed=True, backend="redis")
        return cls(MemoryCounterStore(), fail_closed=False, backend="memory")

    def key(self, tool_ref: str, org_id: str, namespace_id: str) -> str:
        return f"governor:{org_id}:{namespace_id}:{tool_ref}"

    async def check(
        self,
        *,
        tool_ref: str,
        rate_limit: str | None,
        org_id: str = "default",
        namespace_id: str = "local",
    ) -> GovernorDecision:
        if not rate_limit:
            return GovernorDecision(
                allowed=True,
                limit=0,
                remaining=-1,
                reset_after_seconds=0,
                key="",
                reason="no_limit",
            )
        parsed = parse_rate_limit(rate_limit)
        key = self.key(tool_ref, org_id, namespace_id)
        try:
            result = await self.store.consume(key, parsed.count, parsed.window_seconds)
        except Exception:
            if self.fail_closed:
                return GovernorDecision(
                    allowed=False,
                    limit=parsed.count,
                    remaining=0,
                    reset_after_seconds=parsed.window_seconds,
                    key=key,
                    reason="store_unavailable",
                )
            return GovernorDecision(
                allowed=True,
                limit=parsed.count,
                remaining=-1,
                reset_after_seconds=0,
                key=key,
                reason="fail_open",
            )
        return GovernorDecision(
            allowed=result.allowed,
            limit=parsed.count,
            remaining=result.remaining,
            reset_after_seconds=result.reset_after_seconds,
            key=key,
            reason=None if result.allowed else "rate_limit_exceeded",
        )

    def approval_payload(
        self,
        decision: GovernorDecision,
        *,
        tool_name: str,
        tool_ref: str,
    ) -> dict[str, Any]:
        return {
            "reason": decision.reason or "rate_limit_exceeded",
            "tool": tool_name,
            "toolRef": tool_ref,
            "limit": decision.limit,
            "remaining": decision.remaining,
            "resetAfterSeconds": decision.reset_after_seconds,
            "approvalRef": "approval-flows/rate-limit",
        }
