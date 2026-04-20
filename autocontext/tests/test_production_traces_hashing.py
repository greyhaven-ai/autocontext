"""Tests for autocontext.production_traces.hashing — install-salt + id hashing.

Mirrors the TS-side ``production-traces/redaction/install-salt.ts`` and the
``hashValue`` helper in ``redaction/apply.ts``. Output MUST be byte-identical
across runtimes for the same (salt, value) pair.
"""

from __future__ import annotations

import hashlib
import os
import stat
import subprocess
from pathlib import Path

import pytest

# ---- hash_user_id / hash_session_id (pure functions, byte-compat with TS) ----


def test_hash_user_id_is_sha256_of_salt_plus_value() -> None:
    from autocontext.production_traces.hashing import hash_user_id

    salt = "a" * 64
    value = "user-123"
    expected = hashlib.sha256((salt + value).encode("utf-8")).hexdigest()
    assert hash_user_id(value, salt) == expected


def test_hash_user_id_returns_64_char_lowercase_hex() -> None:
    from autocontext.production_traces.hashing import hash_user_id

    salt = "b" * 64
    result = hash_user_id("anything", salt)
    assert len(result) == 64
    assert result == result.lower()
    assert all(c in "0123456789abcdef" for c in result)


def test_hash_user_id_is_deterministic() -> None:
    from autocontext.production_traces.hashing import hash_user_id

    salt = "c" * 64
    assert hash_user_id("alice", salt) == hash_user_id("alice", salt)


def test_hash_user_id_differs_by_salt() -> None:
    from autocontext.production_traces.hashing import hash_user_id

    salt_a = "d" * 64
    salt_b = "e" * 64
    assert hash_user_id("alice", salt_a) != hash_user_id("alice", salt_b)


def test_hash_user_id_differs_by_value() -> None:
    from autocontext.production_traces.hashing import hash_user_id

    salt = "f" * 64
    assert hash_user_id("alice", salt) != hash_user_id("bob", salt)


def test_hash_session_id_uses_same_algorithm_as_user_id() -> None:
    # Semantic distinction is at the call site; under the hood, same sha256.
    from autocontext.production_traces.hashing import hash_session_id, hash_user_id

    salt = "0" * 64
    value = "session-abc"
    assert hash_session_id(value, salt) == hash_user_id(value, salt)


def test_hash_user_id_matches_ts_reference_output() -> None:
    """Cross-runtime byte-identical check.

    Computes the same hash via Node.js's crypto module and asserts byte equality.
    Skips if Node.js is not on PATH (developer environments without TS toolchain).
    """
    from autocontext.production_traces.hashing import hash_user_id

    salt = "0123456789abcdef" * 4  # 64 hex chars, recognizable pattern
    value = "customer-42"
    py_hash = hash_user_id(value, salt)

    node_script = (
        "const crypto = require('node:crypto'); "
        f"const s = {salt!r}; const v = {value!r}; "
        "process.stdout.write(crypto.createHash('sha256').update(s + v).digest('hex'));"
    )
    result = subprocess.run(
        ["node", "-e", node_script],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if result.returncode != 0:
        pytest.skip(f"node not available or failed: {result.stderr}")
    ts_hash = result.stdout.strip()
    assert py_hash == ts_hash, f"Python hash {py_hash} != Node hash {ts_hash}"


# ---- install salt lifecycle ----


def test_load_install_salt_returns_none_when_missing(tmp_path: Path) -> None:
    from autocontext.production_traces.hashing import load_install_salt

    assert load_install_salt(tmp_path) is None


def test_initialize_install_salt_writes_64_char_hex(tmp_path: Path) -> None:
    from autocontext.production_traces.hashing import initialize_install_salt

    salt = initialize_install_salt(tmp_path)
    assert len(salt) == 64
    assert all(c in "0123456789abcdef" for c in salt)
    assert (tmp_path / ".autocontext" / "install-salt").exists()


def test_load_install_salt_roundtrips_initialized_value(tmp_path: Path) -> None:
    from autocontext.production_traces.hashing import initialize_install_salt, load_install_salt

    initial = initialize_install_salt(tmp_path)
    loaded = load_install_salt(tmp_path)
    assert loaded == initial


def test_initialize_install_salt_refuses_to_overwrite(tmp_path: Path) -> None:
    from autocontext.production_traces.hashing import initialize_install_salt

    initialize_install_salt(tmp_path)
    with pytest.raises(FileExistsError, match=r"install-salt|rotate"):
        initialize_install_salt(tmp_path)


def test_rotate_install_salt_generates_new_value(tmp_path: Path) -> None:
    from autocontext.production_traces.hashing import (
        initialize_install_salt,
        load_install_salt,
        rotate_install_salt,
    )

    first = initialize_install_salt(tmp_path)
    rotated = rotate_install_salt(tmp_path)
    assert rotated != first
    assert len(rotated) == 64
    assert load_install_salt(tmp_path) == rotated


def test_rotate_install_salt_works_when_no_prior_salt(tmp_path: Path) -> None:
    from autocontext.production_traces.hashing import load_install_salt, rotate_install_salt

    salt = rotate_install_salt(tmp_path)
    assert len(salt) == 64
    assert load_install_salt(tmp_path) == salt


def test_install_salt_file_has_0600_permissions(tmp_path: Path) -> None:
    if os.name == "nt":  # pragma: no cover -- POSIX-only permission test
        pytest.skip("POSIX-only permission test")
    from autocontext.production_traces.hashing import initialize_install_salt

    initialize_install_salt(tmp_path)
    st = (tmp_path / ".autocontext" / "install-salt").stat()
    assert stat.S_IMODE(st.st_mode) == 0o600


def test_load_install_salt_trims_trailing_newline(tmp_path: Path) -> None:
    from autocontext.production_traces.hashing import load_install_salt

    autoctx = tmp_path / ".autocontext"
    autoctx.mkdir()
    hex_salt = "a" * 64
    (autoctx / "install-salt").write_text(hex_salt + "\n")
    assert load_install_salt(tmp_path) == hex_salt


def test_load_install_salt_rejects_malformed_hex(tmp_path: Path) -> None:
    from autocontext.production_traces.hashing import load_install_salt

    autoctx = tmp_path / ".autocontext"
    autoctx.mkdir()
    (autoctx / "install-salt").write_text("not-hex-too-short")
    with pytest.raises(ValueError, match=r"salt|hex"):
        load_install_salt(tmp_path)


def test_initialize_install_salt_accepts_str_cwd(tmp_path: Path) -> None:
    from autocontext.production_traces.hashing import initialize_install_salt, load_install_salt

    salt = initialize_install_salt(str(tmp_path))
    assert load_install_salt(str(tmp_path)) == salt
