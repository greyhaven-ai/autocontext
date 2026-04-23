from __future__ import annotations

from pathlib import Path

from autocontext.integrations._shared.identity import resolve_identity
from autocontext.integrations._shared.session import autocontext_session
from autocontext.production_traces.hashing import initialize_install_salt


def test_resolve_identity_skips_hashing_without_install_salt(tmp_path: Path) -> None:
    identity = resolve_identity({"user_id": "user-123", "session_id": "session-abc"}, cwd=tmp_path)

    assert identity == {}


def test_resolve_identity_hashes_when_install_salt_exists(tmp_path: Path) -> None:
    initialize_install_salt(tmp_path)

    identity = resolve_identity({"user_id": "user-123", "session_id": "session-abc"}, cwd=tmp_path)

    assert set(identity) == {"user_id_hash", "session_id_hash"}
    assert identity["user_id_hash"] != identity["session_id_hash"]


def test_resolve_identity_prefers_per_call_identity(tmp_path: Path) -> None:
    initialize_install_salt(tmp_path)

    with autocontext_session(user_id="ambient", session_id="ambient-session"):
        explicit = resolve_identity({"user_id": "explicit", "session_id": "explicit-session"}, cwd=tmp_path)
        ambient = resolve_identity(None, cwd=tmp_path)

    assert explicit != ambient
