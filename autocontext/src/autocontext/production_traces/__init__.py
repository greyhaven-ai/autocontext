"""Production-traces SDK surface for customer-side integration.

Public API for Python customers emitting traces from deployed agents. The
vocabulary here mirrors spec §4 verbatim (DDD discipline): ``build_trace``
takes ``provider``, ``model``, ``messages``, etc., matching the ``ProductionTrace``
domain model.

Example::

    from autocontext.production_traces import (
        build_trace,
        write_jsonl,
        TraceBatch,
        hash_user_id,
        hash_session_id,
    )

    trace = build_trace(
        provider="anthropic",
        model="claude-sonnet-4-20250514",
        messages=[...],
        timing={"startedAt": ..., "endedAt": ..., "latencyMs": ...},
        usage={"tokensIn": 10, "tokensOut": 5},
        env={"environmentTag": "production", "appId": "my-app"},
    )
    write_jsonl(trace)
"""

from autocontext.production_traces.emit import TraceBatch, build_trace, write_jsonl
from autocontext.production_traces.hashing import (
    hash_session_id,
    hash_user_id,
    initialize_install_salt,
    load_install_salt,
    rotate_install_salt,
)
from autocontext.production_traces.validate import (
    validate_production_trace,
    validate_production_trace_dict,
)

__all__ = [
    "TraceBatch",
    "build_trace",
    "hash_session_id",
    "hash_user_id",
    "initialize_install_salt",
    "load_install_salt",
    "rotate_install_salt",
    "validate_production_trace",
    "validate_production_trace_dict",
    "write_jsonl",
]
