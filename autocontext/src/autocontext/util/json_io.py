"""Shared JSON file I/O utilities.

Centralises the ``json.loads(path.read_text(…))`` / ``path.write_text(json.dumps(…))``
patterns that were previously repeated 100+ times across the codebase.
"""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Any


def read_json(path: Path) -> Any:
    """Read and parse a JSON file.

    Returns the parsed JSON value (usually a ``dict`` or ``list``).

    Raises ``FileNotFoundError`` if the path does not exist and
    ``json.JSONDecodeError`` on malformed JSON.
    """
    return json.loads(path.read_text(encoding="utf-8"))


def read_json_guarded(path: Path, default: Any = None) -> Any:
    """Read a JSON file, degrading to *default* instead of raising.

    For hot-path readers of persisted state: a missing, corrupt, or
    unreadable file returns *default* so the caller can proceed; the next
    successful write rewrites the file cleanly. ``ValueError`` covers
    ``json.JSONDecodeError``.
    """
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def write_text_atomic(path: Path, content: str) -> None:
    """Write *content* to *path* via a temp file and ``os.replace``.

    A process crash mid-write can never truncate the live file, and concurrent
    readers observe either the old or the new content, never a partial
    write. Parent directories are created automatically.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        tmp.write_text(content, encoding="utf-8")
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def write_json(
    path: Path,
    data: dict[str, Any] | list[Any],
    *,
    sort_keys: bool = True,
) -> None:
    """Serialise *data* as pretty-printed JSON and write to *path* atomically.

    Parent directories are created automatically.
    """
    write_text_atomic(path, json.dumps(data, indent=2, sort_keys=sort_keys))
