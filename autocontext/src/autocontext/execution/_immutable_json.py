"""Immutable snapshots for caller-owned sequences and JSON values."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from types import MappingProxyType
from typing import Any, TypeVar

T = TypeVar("T")


def validated_tuple(value: object, *, label: str, item_type: type[T]) -> tuple[T, ...]:
    """Snapshot a deterministic caller sequence and validate every element."""

    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise TypeError(f"{label} must be a sequence")
    items = tuple(value)
    if any(type(item) is not item_type for item in items):
        raise TypeError(f"{label} must contain only {item_type.__name__} values")
    return items


def freeze_json_object(value: object, *, label: str) -> Mapping[str, Any]:
    """Deep-copy a JSON object into mapping-proxy/tuple containers."""

    if not isinstance(value, Mapping):
        raise TypeError(f"{label} must be an object with string keys")
    snapshot = dict(value)
    if any(type(key) is not str for key in snapshot):
        raise TypeError(f"{label} must be an object with string keys")
    return MappingProxyType({key: _freeze_json_value(item, label=label) for key, item in snapshot.items()})


def _freeze_json_value(value: object, *, label: str) -> Any:
    if value is None or type(value) in {str, bool, int}:
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError(f"{label} contains a non-finite number")
        return value
    if isinstance(value, Mapping):
        return freeze_json_object(value, label=label)
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json_value(item, label=label) for item in value)
    raise TypeError(f"{label} contains a non-JSON value")


def thaw_json(value: object) -> Any:
    """Convert a frozen JSON graph back to ordinary codec containers."""

    if isinstance(value, Mapping):
        return {key: thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [thaw_json(item) for item in value]
    return value
