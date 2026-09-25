"""Capture HTTP usage before an SDK can coerce counter types."""

from __future__ import annotations

import json
from typing import Any

from autocontext.providers.base import ProviderError


class ProviderReceiptError(ProviderError):
    """The provider returned an unusable accounting receipt, not an outage."""


def exact_directional_usage(usage: dict[str, Any]) -> dict[str, int]:
    """Populate legacy counters without coercing malformed captured values.

    Strict consumers still validate the complete raw receipt, including missing
    counters and alias consistency, before accepting or reconciling any budget.
    """
    counters = {}
    for key, alias in (("input_tokens", "prompt_tokens"), ("output_tokens", "completion_tokens")):
        value = usage.get(key, usage.get(alias))
        if type(value) is int:
            counters[key] = value
    return counters


def parse_wire_usage(body: bytes) -> dict[str, Any]:
    """Preserve scalar types and reject ambiguous JSON before SDK parsing.

    Omit null optional fields just as SDK model_dump(exclude_none=True) did;
    missing/null required counters still fail the consumer's receipt validation.
    """
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    def constant(value: str) -> None:
        raise ValueError("non-finite JSON number")

    def without_nulls(value: Any) -> Any:
        if isinstance(value, dict):
            return {key: without_nulls(item) for key, item in value.items() if item is not None}
        if isinstance(value, list):
            return [without_nulls(item) for item in value]
        return value

    try:
        if len(body) > 131072:
            raise ValueError("receipt size limit")
        data = json.loads(body, object_pairs_hook=pairs, parse_constant=constant)
        if not isinstance(data, dict) or not isinstance(data.get("usage"), dict):
            raise ValueError("missing usage object")
        return {key: without_nulls(value) for key, value in data["usage"].items() if value is not None}
    except (ValueError, TypeError, RecursionError) as exc:
        raise ProviderReceiptError("invalid_provider_receipt") from exc
