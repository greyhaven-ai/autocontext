"""BlobRegistry — tracks BlobRefs by run + artifact name (AC-518)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from autocontext.blobstore.ref import BlobRef


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
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> BlobRegistry:
        registry = cls()
        if not path.is_file():
            return registry
        data = json.loads(path.read_text(encoding="utf-8"))
        for run_id, entries in data.items():
            for name, ref_dict in entries.items():
                registry.register(run_id, name, BlobRef.from_dict(ref_dict))
        return registry
