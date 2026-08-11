"""AI Platform — enterprise AI operating system."""

__version__ = "0.8.0"


def __getattr__(name: str):
    if name == "Platform":
        from ai_platform.sdk.platform import Platform

        return Platform
    if name == "EdgeRuntime":
        from ai_platform.edge.runtime import EdgeRuntime

        return EdgeRuntime
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = ["Platform", "EdgeRuntime"]
