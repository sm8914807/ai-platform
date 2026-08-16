"""OTLP / OpenTelemetry tracing for API and SDK."""

from __future__ import annotations

import os
from typing import Any

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    ConsoleSpanExporter,
    SimpleSpanProcessor,
    SpanExporter,
    SpanExportResult,
)
from opentelemetry.sdk.trace import ReadableSpan
from opentelemetry.trace import SpanKind, Status, StatusCode

_provider: TracerProvider | None = None
_memory_exporter: "InMemorySpanExporter | None" = None


class InMemorySpanExporter(SpanExporter):
    """Collect finished spans for tests / local inspection."""

    def __init__(self) -> None:
        self.spans: list[ReadableSpan] = []

    def export(self, spans: list[ReadableSpan]) -> SpanExportResult:
        self.spans.extend(spans)
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        self.spans.clear()

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return True

    def clear(self) -> None:
        self.spans.clear()


def setup_tracing(
    service_name: str = "ai-platform",
    endpoint: str | None = None,
    *,
    environment: str = "development",
    service_version: str = "0.8.0",
    console: bool = False,
    memory: bool = False,
    force: bool = False,
) -> TracerProvider:
    """Configure the global TracerProvider.

    - ``endpoint``: OTLP/HTTP traces URL (e.g. ``http://localhost:4318/v1/traces``)
    - Falls back to ``OTEL_EXPORTER_OTLP_ENDPOINT`` / ``PLATFORM_OTLP_ENDPOINT``
    - ``console``: also print spans (dev)
    - ``memory``: keep spans in-process for assertions
    """
    global _provider, _memory_exporter
    if _provider is not None and not force:
        return _provider
    if force and _provider is not None:
        shutdown_tracing()

    endpoint = (
        endpoint
        or os.getenv("PLATFORM_OTLP_ENDPOINT")
        or os.getenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT")
        or os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
    )
    # If env gives collector root, append standard HTTP path.
    if endpoint and endpoint.rstrip("/").endswith(":4318"):
        endpoint = endpoint.rstrip("/") + "/v1/traces"
    elif endpoint and endpoint.rstrip("/").endswith("4318"):
        endpoint = endpoint.rstrip("/") + "/v1/traces"

    resource = Resource.create(
        {
            "service.name": service_name,
            "service.version": service_version,
            "deployment.environment": environment,
        }
    )
    provider = TracerProvider(resource=resource)

    if endpoint:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
    if console:
        provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))
    if memory:
        _memory_exporter = InMemorySpanExporter()
        provider.add_span_processor(SimpleSpanProcessor(_memory_exporter))

    _set_tracer_provider(provider)
    _provider = provider
    return provider


def _set_tracer_provider(provider: TracerProvider) -> None:
    """Set global provider, resetting OTEL's once-guard when needed (tests / lifespan)."""
    from opentelemetry.util._once import Once

    if getattr(trace, "_TRACER_PROVIDER", None) is not None:
        trace._TRACER_PROVIDER = None  # type: ignore[attr-defined]
        trace._TRACER_PROVIDER_SET_ONCE = Once()  # type: ignore[attr-defined]
    trace.set_tracer_provider(provider)


def shutdown_tracing() -> None:
    """Flush and shut down the global provider (call from API lifespan)."""
    global _provider, _memory_exporter
    if _provider is not None:
        try:
            _provider.force_flush()
        except Exception:
            pass
        try:
            _provider.shutdown()
        except Exception:
            pass
    _provider = None
    _memory_exporter = None
    try:
        from opentelemetry.util._once import Once

        trace._TRACER_PROVIDER = None  # type: ignore[attr-defined]
        trace._TRACER_PROVIDER_SET_ONCE = Once()  # type: ignore[attr-defined]
    except Exception:
        pass


def get_tracer(name: str) -> trace.Tracer:
    return trace.get_tracer(name)


def memory_spans() -> list[ReadableSpan]:
    if _memory_exporter is None:
        return []
    return list(_memory_exporter.spans)


def clear_memory_spans() -> None:
    if _memory_exporter is not None:
        _memory_exporter.clear()


def tracing_status() -> dict[str, Any]:
    provider = _provider
    return {
        "enabled": provider is not None,
        "memoryExporter": _memory_exporter is not None,
        "spanCount": len(_memory_exporter.spans) if _memory_exporter else 0,
    }


async def trace_http_middleware(request: Any, call_next: Any) -> Any:
    """ASGI-style FastAPI middleware body for HTTP server spans."""
    tracer = get_tracer("ai-platform.api")
    route = request.url.path
    method = request.method
    with tracer.start_as_current_span(
        f"{method} {route}",
        kind=SpanKind.SERVER,
    ) as span:
        span.set_attribute("http.method", method)
        span.set_attribute("http.target", route)
        span.set_attribute("http.scheme", request.url.scheme)
        if request.client:
            span.set_attribute("net.peer.ip", request.client.host)
        try:
            response = await call_next(request)
        except Exception as exc:
            span.record_exception(exc)
            span.set_status(Status(StatusCode.ERROR, str(exc)))
            raise
        span.set_attribute("http.status_code", response.status_code)
        if response.status_code >= 500:
            span.set_status(Status(StatusCode.ERROR))
        elif response.status_code >= 400:
            span.set_attribute("http.status_class", "4xx")
        return response
