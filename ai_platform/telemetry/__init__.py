"""OpenTelemetry / OTLP package."""

from ai_platform.telemetry.tracing import (
    clear_memory_spans,
    get_tracer,
    memory_spans,
    setup_tracing,
    shutdown_tracing,
    tracing_status,
)

__all__ = [
    "clear_memory_spans",
    "get_tracer",
    "memory_spans",
    "setup_tracing",
    "shutdown_tracing",
    "tracing_status",
]
