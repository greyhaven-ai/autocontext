"""BlobRegistry — tracks BlobRefs by run + artifact name (AC-518)."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from autocontext.blobstore.ref import BlobRef
from autocontext.util.json_io import write_text_atomic

logger = logging.getLogger(__name__)


class BlobRegistry:
    """In-memory registry of BlobRefs, persistable to JSON."""

    def __init__(self) -> None:
        self._entries: dict[str, dict[str, BlobRef]] = {}  # run_id → {name → ref}

    def register(self, run_id: str, name: str, ref: BlobRef) -> None:
        if run_id not in self._entries:
            self._entries[run_id] = {}
        self._entries[run_id][name] = ref

    def lookup(self, run_id: str, name: str) -> BlobRef | None:
        return self._entries.get(run_id, {}).get(name)

    def list_for_run(self, run_id: str) -> list[BlobRef]:
        return list(self._entries.get(run_id, {}).values())

    def save(self, path: Path) -> None:
        data: dict[str, Any] = {}
        for run_id, entries in self._entries.items():
            data[run_id] = {name: ref.to_dict() for name, ref in entries.items()}
        write_text_atomic(path, json.dumps(data, indent=2))

    @classmethod
    def load(cls, path: Path) -> BlobRegistry:
        registry = cls()
        if not path.is_file():
            return registry
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            logger.warning("blob registry unreadable, starting empty: %s", path)
            return registry
        if not isinstance(data, dict):
            logger.warning("blob registry misshapen, starting empty: %s", path)
            return registry
        for run_id, entries in data.items():
            if not isinstance(entries, dict):
                logger.warning("skipping misshapen blob registry run %s in %s", run_id, path)
                continue
            for name, ref_dict in entries.items():
                if not isinstance(ref_dict, dict):
                    logger.warning("skipping misshapen blob ref %s/%s in %s", run_id, name, path)
                    continue
                try:
                    registry.register(run_id, name, BlobRef.from_dict(ref_dict))
                except (KeyError, TypeError, ValueError):
                    logger.warning("skipping invalid blob ref %s/%s in %s", run_id, name, path)
                    continue
        return registry
