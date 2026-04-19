"""Thin validation wrapper around the generated Pydantic models.

Parses a ``dict`` into a ``ProductionTrace`` — raises ``pydantic.ValidationError``
on invalid input. Mirrors the TS-side ``validateProductionTrace`` except that
the Python API returns the parsed model on success (Pydantic-idiomatic) rather
than a ``ValidationResult`` union.
"""

from __future__ import annotations

from typing import Any

from autocontext.production_traces.contract.models import ProductionTrace


def validate_production_trace(data: dict[str, Any]) -> ProductionTrace:
    """Validate and parse a production-trace document.

    Raises ``pydantic.ValidationError`` if the input fails schema validation
    (including any branded-id pattern constraints on the contained fields).
    """
    return ProductionTrace.model_validate(data)


__all__ = ["validate_production_trace"]
