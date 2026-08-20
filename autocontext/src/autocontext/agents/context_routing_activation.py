"""Validation for applying immutable context-bundle routing at runtime."""

from __future__ import annotations

from typing import Any

from autocontext.config.settings import AppSettings
from autocontext.context_bundles.assembly import (
    CONSTRUCTION_BOUND_ROUTING_FIELDS,
    DEFERRED_ROUTING_FIELDS,
    LIVE_CONTEXT_ROUTING_FIELDS,
)


def resolve_active_context_settings(
    baseline: AppSettings,
    constructed: AppSettings,
    routing: dict[str, Any],
) -> AppSettings:
    """Validate active-bundle routing and return generation-effective settings."""

    for field in CONSTRUCTION_BOUND_ROUTING_FIELDS:
        if field in routing and routing[field] != getattr(constructed, field):
            raise RuntimeError(f"active context cannot replace constructed routing field {field!r}")
    for field in DEFERRED_ROUTING_FIELDS:
        if routing.get(field):
            raise RuntimeError(f"active context routing field {field!r} requires lifecycle reconstruction")
    updates: dict[str, Any] = {}
    for field in LIVE_CONTEXT_ROUTING_FIELDS:
        if field not in routing:
            continue
        value = routing[field]
        if field.startswith("model_"):
            if not isinstance(value, str) or not value.strip():
                raise RuntimeError(f"active context routing field {field!r} must be a non-empty string")
            updates[field] = value.strip()
        elif not isinstance(value, bool):
            raise RuntimeError(f"active context routing field {field!r} must be a boolean")
        else:
            updates[field] = value
    effective = baseline.model_copy(update=updates) if updates else baseline
    if effective.agent_provider != constructed.agent_provider:
        raise RuntimeError("active context cannot replace the constructed agent provider")
    return effective


__all__ = ["resolve_active_context_settings"]
