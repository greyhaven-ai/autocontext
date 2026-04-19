"""Branded / constrained aliases for production-trace IDs.

Pydantic v2 picks up the ``pattern`` constraint on parse, giving us schema-level
validation equivalent to the TS branded-id parsers. ``NewType`` is used for the
opaque ``FeedbackRefId`` since it has no pattern constraint.
"""

from __future__ import annotations

from typing import Annotated, NewType

from pydantic import StringConstraints

# ULID pattern — Crockford base32 excludes I/L/O/U. 26 chars, uppercase.
ProductionTraceId = Annotated[str, StringConstraints(pattern=r"^[0-9A-HJKMNP-TV-Z]{26}$")]

# AppId: slug-like, lowercase, non-empty, path-safe.
AppId = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9_-]*$")]

# SHA-256 hex (lowercase, exactly 64 chars).
UserIdHash = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
SessionIdHash = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]

# Opaque customer-supplied reference — no format enforced beyond non-emptiness.
FeedbackRefId = NewType("FeedbackRefId", str)

# Reused from shared defs / control-plane conventions.
EnvironmentTag = Annotated[str, StringConstraints(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_-]*$")]
ContentHash = Annotated[str, StringConstraints(pattern=r"^sha256:[0-9a-f]{64}$")]
Scenario = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9_-]*$")]

__all__ = [
    "AppId",
    "ContentHash",
    "EnvironmentTag",
    "FeedbackRefId",
    "ProductionTraceId",
    "Scenario",
    "SessionIdHash",
    "UserIdHash",
]
