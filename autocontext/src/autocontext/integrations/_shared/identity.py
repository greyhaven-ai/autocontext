from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from autocontext.integrations._shared.session import current_session
from autocontext.production_traces.hashing import (
    hash_session_id,
    hash_user_id,
    load_install_salt,
)


def resolve_identity(
    per_call: Mapping[str, Any] | None,
    *,
    cwd: str | Path = ".",
) -> dict[str, str]:
    """Resolve and hash per-call or ambient identity when an install salt exists."""
    raw: dict[str, str] = {}
    if per_call:
        if per_call.get("user_id") is not None:
            raw["user_id"] = str(per_call["user_id"])
        if per_call.get("session_id") is not None:
            raw["session_id"] = str(per_call["session_id"])
    if not raw:
        raw = current_session()
    if not raw:
        return {}

    salt = load_install_salt(cwd)
    if not salt:
        return {}

    hashed: dict[str, str] = {}
    if raw.get("user_id"):
        hashed["user_id_hash"] = hash_user_id(raw["user_id"], salt)
    if raw.get("session_id"):
        hashed["session_id_hash"] = hash_session_id(raw["session_id"], salt)
    return hashed
