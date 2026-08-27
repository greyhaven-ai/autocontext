"""Security helpers for emitting generated Python scenario source.

Values in custom-scenario specs originate in model output and must remain data
when they are embedded in generated modules.  Keep executable identifiers and
Python literals behind these small, auditable helpers instead of interpolating
raw spec text into source templates.
"""

from __future__ import annotations

import json
import keyword
import math
import re
from typing import Any

_SCENARIO_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def validate_generated_scenario_name(name: str) -> str:
    """Validate a generated scenario's module, registry, and directory name."""
    if not isinstance(name, str) or not _SCENARIO_NAME_RE.fullmatch(name) or keyword.iskeyword(name):
        raise ValueError("generated scenario name must be a non-keyword ASCII Python identifier")
    return name


def generated_class_name(name: str, suffix: str) -> str:
    """Return a valid class identifier derived from an untrusted scenario name."""
    words = re.findall(r"[A-Za-z0-9]+", name)
    stem = "".join(word.capitalize() for word in words) or "Generated"
    if stem[0].isdigit() or keyword.iskeyword(stem):
        stem = f"Generated{stem}"
    candidate = f"{stem}{suffix}"
    if not candidate.isidentifier() or keyword.iskeyword(candidate):
        raise ValueError("could not derive a safe generated class name")
    return candidate


def python_literal(value: Any) -> str:
    """Serialize JSON-like data as a Python literal without invoking custom reprs."""
    if value is None or type(value) in {bool, int, str}:
        return repr(value)
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError("generated numeric literals must be finite")
        return repr(value)
    if type(value) is list:
        return "[" + ", ".join(python_literal(item) for item in value) + "]"
    if type(value) is tuple:
        items = ", ".join(python_literal(item) for item in value)
        if len(value) == 1:
            items += ","
        return f"({items})"
    if type(value) is dict:
        items = ", ".join(
            f"{python_literal(key)}: {python_literal(item)}"
            for key, item in value.items()
        )
        return "{" + items + "}"
    raise TypeError(f"unsupported generated literal type: {type(value).__name__}")


def python_string_literal(value: str) -> str:
    """Serialize text with stable double quotes as a valid Python literal."""
    if not isinstance(value, str):
        raise TypeError("generated string literal must be text")
    return json.dumps(value, ensure_ascii=True)
