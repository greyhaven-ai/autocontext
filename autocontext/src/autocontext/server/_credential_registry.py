"""Secure loading and parsing for control-plane credential registries."""

from __future__ import annotations

import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Final

SERVER_AUTH_TOKEN_ENV: Final = "AUTOCONTEXT_SERVER_TOKEN"
SERVER_CREDENTIALS_FILE_ENV: Final = "AUTOCONTEXT_SERVER_CREDENTIALS_FILE"

_MAX_CREDENTIALS_FILE_BYTES = 64 * 1024
_MAX_CREDENTIALS = 256
_REGISTRY_REQUIRED_KEYS = frozenset({"kid", "principal", "secret", "capabilities"})
_REGISTRY_OPTIONAL_KEYS = frozenset({"not_before", "not_after", "disabled"})


@dataclass(frozen=True)
class LoadedCredential:
    """One structurally parsed credential awaiting policy validation."""

    kid: str
    principal: str
    secret: bytes
    capabilities: frozenset[str]
    not_before: int | None
    not_after: int | None
    disabled: bool


def load_credentials_registry(raw_path: str) -> list[LoadedCredential]:
    """Securely read and structurally parse a version-one registry."""
    if os.name == "nt":
        raise RuntimeError(
            f"{SERVER_CREDENTIALS_FILE_ENV} is unsupported on Windows until owner and DACL "
            f"validation is available; use {SERVER_AUTH_TOKEN_ENV} instead"
        )
    path = Path(raw_path)
    if not path.is_absolute() or ".." in path.parts:
        raise RuntimeError(f"{SERVER_CREDENTIALS_FILE_ENV} must name an absolute path without '..'")
    _reject_symlink_components(path)

    flags = os.O_RDONLY
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise RuntimeError(f"cannot securely open {SERVER_CREDENTIALS_FILE_ENV}: {error}") from error
    try:
        before = os.fstat(descriptor)
        _validate_registry_stat(path, before)
        pathname_stat = path.lstat()
        if (pathname_stat.st_dev, pathname_stat.st_ino) != (before.st_dev, before.st_ino):
            raise RuntimeError("credentials registry path changed while it was being opened")
        raw = os.read(descriptor, _MAX_CREDENTIALS_FILE_BYTES + 1)
        after = os.fstat(descriptor)
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        ):
            raise RuntimeError("credentials registry changed while it was being read")
    finally:
        os.close(descriptor)
    if len(raw) > _MAX_CREDENTIALS_FILE_BYTES:
        raise RuntimeError("credentials registry exceeds size limit")

    try:
        document = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeError("credentials registry is not valid UTF-8 JSON") from error
    if not isinstance(document, dict) or set(document) != {"version", "credentials"}:
        raise RuntimeError("credentials registry must contain only version and credentials")
    if type(document["version"]) is not int or document["version"] != 1:
        raise RuntimeError("unsupported credentials registry version")
    entries = document["credentials"]
    if not isinstance(entries, list) or len(entries) > _MAX_CREDENTIALS:
        raise RuntimeError(f"credentials registry may contain at most {_MAX_CREDENTIALS} entries")
    return [_parse_registry_credential(entry) for entry in entries]


def _reject_symlink_components(path: Path) -> None:
    current = Path(path.anchor)
    components = path.parts[1:]
    for index, component in enumerate(components):
        current /= component
        try:
            metadata = current.lstat()
            if stat.S_ISLNK(metadata.st_mode):
                raise RuntimeError(f"credentials registry path contains symlink {current}")
            if index < len(components) - 1:
                if not stat.S_ISDIR(metadata.st_mode):
                    raise RuntimeError(f"credentials registry parent is not a directory: {current}")
                if stat.S_IMODE(metadata.st_mode) & 0o022:
                    raise RuntimeError(f"credentials registry parent is group/world writable: {current}")
                if hasattr(os, "getuid") and metadata.st_uid not in {0, os.getuid()}:
                    raise RuntimeError(f"credentials registry parent has an untrusted owner: {current}")
        except FileNotFoundError:
            # The final os.open call produces the useful missing-path error.
            return


def _validate_registry_stat(path: Path, metadata: os.stat_result) -> None:
    if not stat.S_ISREG(metadata.st_mode):
        raise RuntimeError(f"credentials registry {path} is not a regular file")
    if metadata.st_size > _MAX_CREDENTIALS_FILE_BYTES:
        raise RuntimeError("credentials registry exceeds size limit")
    if hasattr(os, "getuid") and metadata.st_uid != os.getuid():
        raise RuntimeError("credentials registry must be owned by the current user")
    mode = stat.S_IMODE(metadata.st_mode)
    if mode not in {0o400, 0o600}:
        raise RuntimeError("credentials registry permissions must be 0400 or 0600")


def _parse_registry_credential(value: object) -> LoadedCredential:
    if not isinstance(value, dict):
        raise RuntimeError("each credentials registry entry must be an object")
    keys = set(value)
    allowed_keys = _REGISTRY_REQUIRED_KEYS | _REGISTRY_OPTIONAL_KEYS
    if not _REGISTRY_REQUIRED_KEYS.issubset(keys) or not keys.issubset(allowed_keys):
        raise RuntimeError("credentials registry entry has missing or unexpected fields")
    kid = value["kid"]
    principal = value["principal"]
    raw_secret = value["secret"]
    capabilities = value["capabilities"]
    if not isinstance(kid, str) or not isinstance(principal, str) or not isinstance(raw_secret, str):
        raise RuntimeError("credential kid, principal, and secret must be strings")
    if (
        not isinstance(capabilities, list)
        or not capabilities
        or any(not isinstance(capability, str) for capability in capabilities)
        or capabilities != sorted(set(capabilities))
    ):
        raise RuntimeError("credential capabilities must be a sorted unique non-empty array")
    not_before = value.get("not_before")
    not_after = value.get("not_after")
    disabled = value.get("disabled", False)
    if not_before is not None and type(not_before) is not int:
        raise RuntimeError("credential not_before must be an integer")
    if not_after is not None and type(not_after) is not int:
        raise RuntimeError("credential not_after must be an integer")
    if type(disabled) is not bool:
        raise RuntimeError("credential disabled must be a boolean")
    return LoadedCredential(
        kid=kid,
        principal=principal,
        secret=raw_secret.encode("utf-8"),
        capabilities=frozenset(capabilities),
        not_before=not_before,
        not_after=not_after,
        disabled=disabled,
    )
