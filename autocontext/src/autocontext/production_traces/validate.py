"""Validation helpers for ``ProductionTrace`` documents.

The generated Pydantic model in ``contract.models`` is the single source of
truth for shape (DRY: never redefined here). These helpers are customer-facing
conveniences:

* :func:`validate_production_trace` — raising variant (idiomatic Pydantic).
* :func:`validate_production_trace_dict` — ergonomic non-raising variant that
  returns ``(ok, errors)``; messages carry a dot-path field pointer flattened
  from Pydantic's structured errors.
"""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from autocontext.production_traces.contract.models import ProductionTrace


def validate_production_trace(data: dict[str, Any]) -> ProductionTrace:
    """Validate and parse a production-trace document.

    Raises :class:`pydantic.ValidationError` if the input fails schema validation
    (including any branded-id pattern constraints on the contained fields).
    """
    return ProductionTrace.model_validate(data)


def validate_production_trace_dict(data: Any) -> tuple[bool, list[str]]:
    """Non-raising variant. Returns ``(True, [])`` on valid input; otherwise
    ``(False, [<messages>])`` with one entry per Pydantic error.

    Error messages are formatted as ``"<dot.path>: <message>"`` where
    ``<dot.path>`` is derived from the error ``loc`` tuple — e.g.
    ``"messages.0.role"`` for a bad enum value in the first message.
    """
    try:
        ProductionTrace.model_validate(data)
    except ValidationError as exc:
        return False, [_format_error(err) for err in exc.errors()]
    return True, []


def _format_error(err: Any) -> str:
    loc = err.get("loc") or ()
    path = ".".join(str(p) for p in loc) if loc else "<root>"
    msg = err.get("msg", "invalid")
    return f"{path}: {msg}"


__all__ = [
    "validate_production_trace",
    "validate_production_trace_dict",
]
