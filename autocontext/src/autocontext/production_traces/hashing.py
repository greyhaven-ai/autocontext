"""Install-salt management + user/session identifier hashing.

Byte-for-byte mirror of the TypeScript implementation in
``ts/src/production-traces/redaction/install-salt.ts`` (salt lifecycle) and
``ts/src/production-traces/redaction/apply.ts`` (``hashValue``).

**Install salt** is a per-installation 256-bit secret (64 lowercase hex chars)
used to deterministically obfuscate user/session identifiers across emitter
SDKs. Stored at ``<cwd>/.autocontext/install-salt`` with file mode ``0600``.

Hashing algorithm: ``sha256(salt + value)`` encoded as lowercase hex. Matches
Node's ``createHash('sha256').update(salt + value).digest('hex')``.
"""

from __future__ import annotations

import hashlib
import os
import re
import secrets
from pathlib import Path

_ROOT_DIR = ".autocontext"
_SALT_FILE = "install-salt"
_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")


def _install_salt_path(cwd: str | Path) -> Path:
    return Path(cwd) / _ROOT_DIR / _SALT_FILE


def initialize_install_salt(cwd: str | Path) -> str:
    """Generate a fresh 256-bit hex salt and persist it.

    Writes ``<cwd>/.autocontext/install-salt`` with mode ``0600``. Refuses to
    overwrite an existing salt — callers must use :func:`rotate_install_salt`
    (CLI enforces ``--force``).

    Returns the salt as 64-char lowercase hex.
    Raises ``FileExistsError`` if the salt file already exists.
    """
    path = _install_salt_path(cwd)
    if path.exists():
        raise FileExistsError(
            f"install-salt already exists at {path}; "
            "use 'autoctx production-traces rotate-salt --force' to replace it"
        )
    return _write_salt(path)


def rotate_install_salt(cwd: str | Path) -> str:
    """Unconditionally generate and persist a fresh salt.

    Overwrites any existing salt file. The CLI is responsible for gating this
    behind ``--force`` per spec §4.6.
    """
    return _write_salt(_install_salt_path(cwd))


def load_install_salt(cwd: str | Path) -> str | None:
    """Read the install salt, or return ``None`` if the file does not exist.

    Tolerates a trailing newline (hand-edited config). Raises ``ValueError`` if
    the contents are not a valid 64-char lowercase hex string.
    """
    path = _install_salt_path(cwd)
    if not path.exists():
        return None
    raw = path.read_text(encoding="utf-8").strip()
    if not _HEX64_RE.fullmatch(raw):
        raise ValueError(
            f"install-salt at {path} is not valid 64-char lowercase hex"
        )
    return raw


def hash_user_id(user_id: str, salt: str) -> str:
    """Return ``sha256(salt + user_id)`` as 64-char lowercase hex.

    Byte-identical to the TS ``hashValue(userId, salt)`` helper.
    """
    _assert_non_empty_salt(salt)
    return hashlib.sha256((salt + user_id).encode("utf-8")).hexdigest()


def hash_session_id(session_id: str, salt: str) -> str:
    """Same algorithm as :func:`hash_user_id`; semantic distinction at call site."""
    _assert_non_empty_salt(salt)
    return hashlib.sha256((salt + session_id).encode("utf-8")).hexdigest()


# ---- internals ----


def _write_salt(path: Path) -> str:
    salt = secrets.token_hex(32)  # 32 bytes = 256 bits = 64 hex chars
    path.parent.mkdir(parents=True, exist_ok=True)
    # Write, then chmod — `Path.write_text` does not accept a mode kwarg.
    path.write_text(salt + "\n", encoding="utf-8")
    # Best-effort 0600 on POSIX; Windows no-op matches TS behavior.
    try:
        os.chmod(path, 0o600)
    except (OSError, NotImplementedError):  # pragma: no cover - Windows guard
        pass
    return salt


def _assert_non_empty_salt(salt: str) -> None:
    if not isinstance(salt, str) or len(salt) == 0:
        raise ValueError(
            "hashing salt must be a non-empty string; "
            "use load_install_salt() or initialize_install_salt()"
        )


__all__ = [
    "hash_session_id",
    "hash_user_id",
    "initialize_install_salt",
    "load_install_salt",
    "rotate_install_salt",
]
