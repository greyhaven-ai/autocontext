#!/usr/bin/env python3
"""Cross-runtime parity helper: invoke Python ``build_trace`` and emit canonical JSON.

Reads a JSON document from stdin with TypeScript-shape ``BuildTraceInputs``
(camelCase), translates the top-level argument names to snake_case for Python,
calls ``autocontext.production_traces.emit.build_trace(**kwargs)``, then serializes
the result using a canonical-JSON encoder that mirrors the TypeScript
``canonicalJsonStringify`` byte-for-byte.

Byte identity is verified via:
  - UTF-16-code-unit key sort (TS uses `<`, Python uses `sorted(...)` on str
    which also sorts by code point — identical for all BMP chars our schema
    allows)
  - Minimal separators: ``(",", ":")``
  - ASCII-safe escapes: ``ensure_ascii=False`` so non-ASCII survives; TS's
    ``JSON.stringify`` also escapes control chars and emits raw UTF-8 for
    printable codepoints
"""
from __future__ import annotations

import json
import sys
from typing import Any

# Ensure Python package path — this script is invoked from the TS worktree via
# subprocess; the autocontext Python package lives in a sibling dir.
import os
HERE = os.path.dirname(os.path.abspath(__file__))
PY_SRC = os.path.join(HERE, "..", "..", "..", "autocontext", "src")
if os.path.isdir(PY_SRC):
    sys.path.insert(0, PY_SRC)

from autocontext.production_traces.emit import build_trace  # noqa: E402


# Top-level BuildTraceInputs field renames. Nested value-object fields stay
# camelCase because the JSON schema expects them that way (Pydantic models
# use alias-map under the hood). We only translate the Python keyword-arg
# names at the build_trace callsite.
CAMEL_TO_SNAKE = {
    "provider": "provider",
    "model": "model",
    "messages": "messages",
    "timing": "timing",
    "usage": "usage",
    "env": "env",
    "traceId": "trace_id",
    "session": "session",
    "outcome": "outcome",
    "toolCalls": "tool_calls",
    "feedbackRefs": "feedback_refs",
    "routing": None,  # Python build_trace does not accept routing — emit does
    "metadata": "metadata",
    "source": "source",
    "collectedAt": None,  # Ignored on both sides (spec §4.1 forward-compat)
}


def _canonical_json(value: Any) -> str:
    """Canonical JSON serialization matching TypeScript's canonicalJsonStringify.

    Sort object keys by str ordering (equivalent to UTF-16 code-unit sort for
    BMP chars), minimal separators, reject non-finite floats, reject
    ``undefined`` (not representable). Preserve unicode (no ASCII escape).
    """
    return json.dumps(
        _encode(value),
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _encode(value: Any) -> Any:
    """Recursively normalize so json.dumps produces canonical output.

    json.dumps with sort_keys=True already sorts at every depth, but we must
    still reject types that would produce non-deterministic output (sets,
    tuples get stringified differently).
    """
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, dict):
        return {k: _encode(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_encode(v) for v in value]
    raise TypeError(f"Non-canonical type: {type(value).__name__}")


def main() -> None:
    raw = sys.stdin.read()
    inputs = json.loads(raw)

    # Routing is a TS-only input on build_trace — Python's emit.build_trace
    # currently doesn't have it as a kwarg. If the TS caller sends routing,
    # we splice it into the dict result post-hoc to keep parity with TS's
    # behavior of passing it through.
    routing = inputs.pop("routing", None)
    # collectedAt is accepted but not emitted on either side.
    inputs.pop("collectedAt", None)

    kwargs: dict[str, Any] = {}
    for k, v in inputs.items():
        snake = CAMEL_TO_SNAKE.get(k)
        if snake is None:
            continue  # silently drop unknown / intentionally-discarded fields
        kwargs[snake] = v

    trace = build_trace(**kwargs)

    # Inject routing into the result if the TS side would have done so — keeps
    # cross-runtime byte-identity on the `routing` field (AC-545) until Python's
    # build_trace accepts it as a kwarg.
    if routing is not None:
        trace["routing"] = routing

    sys.stdout.write(_canonical_json(trace))


if __name__ == "__main__":
    main()
