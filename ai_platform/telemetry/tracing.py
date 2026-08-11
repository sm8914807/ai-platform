"""OTLP tracing setup."""

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

_provider: TracerProvider | None = None


def setup_tracing(service_name: str = "ai-platform", endpoint: str | None = None) -> None:
    global _provider
    resource = Resource.create({"service.name": service_name})
    _provider = TracerProvider(resource=resource)
    if endpoint:
        exporter = OTLPSpanExporter(endpoint=endpoint)
        _provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(_provider)


def get_tracer(name: str) -> trace.Tracer:
    return trace.get_tracer(name)
