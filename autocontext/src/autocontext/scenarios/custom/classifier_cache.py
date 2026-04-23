"""Content-addressable cache for LLM classifier fallback results (AC-581).

The AC-580 fallback makes one LLM call per keyword miss. Many autocontext
workflows re-classify the same natural-language description multiple times
(e.g. ``autoctx solve`` followed by ``autoctx new-scenario`` on the same
spec). This module persists the fallback's result keyed by a SHA-256 hash of
the normalized classification input so duplicate calls never re-invoke the LLM.

File format (``cache.json``)::

    {
        "schema_version": "<hash of sorted registered family names>",
        "entries": {
            "<sha256(description)>": {
                "family_name": "simulation",
                "confidence": 0.82,
                "rationale": "matches simulation pattern",
                "alternatives": [...],
                "no_signals_matched": false,
                "cached_at": "2026-04-22T12:34:56Z"
            },
            ...
        }
    }

When ``schema_version`` does not match the current registry, all entries are
treated as invalid (stale) and overwritten on the next put.

The cache is best-effort: any read or parse error produces a cache miss
(never an exception), and writes are atomic via ``os.replace``.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from autocontext.scenarios.custom.family_classifier import FamilyClassification

logger = logging.getLogger(__name__)


def default_classifier_cache_path(knowledge_root: Path) -> Path:
    """Shared on-disk cache location for family-classification fallback results."""
    return knowledge_root / "_shared" / "family_classifier_cache.json"


def _schema_version(registered_families: list[str]) -> str:
    """Hash of the sorted family name set. Order-independent."""
    joined = ",".join(sorted(name.strip() for name in registered_families))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def _description_key(description: str) -> str:
    return hashlib.sha256(description.encode("utf-8")).hexdigest()


class ClassifierCache:
    """Filesystem-backed cache for LLM classifier fallback results."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def get(
        self,
        description: str,
        registered_families: list[str],
    ) -> FamilyClassification | None:
        """Return the cached classification, or None on miss / schema change / error."""
        data = self._read()
        if data is None:
            return None
        if data.get("schema_version") != _schema_version(registered_families):
            return None
        entry = data.get("entries", {}).get(_description_key(description))
        if not isinstance(entry, dict):
            return None
        payload = {k: v for k, v in entry.items() if k != "cached_at"}
        try:
            return FamilyClassification.from_dict(payload)
        except Exception as exc:
            logger.warning("ClassifierCache: dropping malformed entry (%s)", exc)
            return None

    def put(
        self,
        description: str,
        registered_families: list[str],
        classification: FamilyClassification,
    ) -> None:
        """Write the classification to disk, invalidating stale schema entries."""
        schema = _schema_version(registered_families)

        data = self._read() or {}
        # Drop all entries whenever the schema version changes: the LLM may
        # have selected a family that no longer exists, or new families may
        # better fit old descriptions.
        if data.get("schema_version") != schema:
            data = {"schema_version": schema, "entries": {}}

        entry: dict[str, Any] = classification.to_dict()
        entry["cached_at"] = datetime.now(UTC).isoformat()
        data["entries"][_description_key(description)] = entry

        self._write(data)

    def _read(self) -> dict[str, Any] | None:
        try:
            raw = self._path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        except OSError as exc:
            logger.warning("ClassifierCache: read failed (%s)", exc)
            return None
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            logger.warning("ClassifierCache: corrupt cache file (%s), ignoring", exc)
            return None
        if not isinstance(parsed, dict):
            return None
        return parsed

    def _write(self, data: dict[str, Any]) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(self._path.suffix + ".tmp")
            tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
            os.replace(tmp, self._path)
        except OSError as exc:
            logger.warning("ClassifierCache: write failed (%s)", exc)
