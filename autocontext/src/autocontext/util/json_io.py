"""Shared JSON file I/O utilities.

Centralises the ``json.loads(path.read_text(…))`` / ``path.write_text(json.dumps(…))``
patterns that were previously repeated 100+ times across the codebase.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def read_json(path: Path) -> Any:
    """Read and parse a JSON file.

    Returns the parsed JSON value (usually a ``dict`` or ``list``).

    Raises ``FileNotFoundError`` if the path does not exist and
    ``json.JSONDecodeError`` on malformed JSON.
    """
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(
    path: Path,
    data: dict[str, Any] | list[Any],
    *,
    sort_keys: bool = True,
) -> None:
    """Serialise *data* as pretty-printed JSON and write to *path*.

    Parent directories are created automatically.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=sort_keys), encoding="utf-8")
