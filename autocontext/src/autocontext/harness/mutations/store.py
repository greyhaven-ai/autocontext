"""Versioned mutation persistence (AC-505)."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from autocontext.blobstore.store import resolve_blob_path
from autocontext.harness.mutations.spec import HarnessMutation
from autocontext.util.json_io import write_text_atomic

logger = logging.getLogger(__name__)

_MUTATIONS_FILENAME = "mutations.json"
_VERSIONS_DIR = "mutation_versions"


class MutationStore:
    """Version and persist harness mutations as JSON artifacts."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def _scenario_dir(self, scenario_name: str) -> Path:
        return resolve_blob_path(self.root, scenario_name)

    def save(self, scenario_name: str, mutations: list[HarnessMutation]) -> None:
        """Save mutations, preserving previous version."""
        scenario_dir = self._scenario_dir(scenario_name)
        scenario_dir.mkdir(parents=True, exist_ok=True)
        mutations_path = scenario_dir / _MUTATIONS_FILENAME

        # Archive current version
        if mutations_path.exists():
            versions_dir = scenario_dir / _VERSIONS_DIR
            versions_dir.mkdir(exist_ok=True)
            version_num = len(list(versions_dir.glob("mutations_v*.json"))) + 1
            archive_path = versions_dir / f"mutations_v{version_num}.json"
            write_text_atomic(archive_path, mutations_path.read_text(encoding="utf-8"))

        # Write current
        data = [m.to_dict() for m in mutations]
        write_text_atomic(mutations_path, json.dumps(data, indent=2))

    def load(self, scenario_name: str) -> list[HarnessMutation]:
        """Load current mutations for a scenario."""
        mutations_path = self._scenario_dir(scenario_name) / _MUTATIONS_FILENAME
        if not mutations_path.exists():
            return []
        try:
            data = json.loads(mutations_path.read_text(encoding="utf-8"))
            return [HarnessMutation.from_dict(d) for d in data]
        except (json.JSONDecodeError, KeyError, ValueError):
            return []

    def list_versions(self, scenario_name: str) -> list[str]:
        """List available version files."""
        scenario_dir = self._scenario_dir(scenario_name)
        versions_dir = scenario_dir / _VERSIONS_DIR
        if not versions_dir.is_dir():
            current = scenario_dir / _MUTATIONS_FILENAME
            return [str(current)] if current.exists() else []
        versions = sorted(versions_dir.glob("mutations_v*.json"))
        result = [str(v) for v in versions]
        current = scenario_dir / _MUTATIONS_FILENAME
        if current.exists():
            result.append(str(current))
        return result

    def rollback(self, scenario_name: str) -> bool:
        """Rollback to the previous version. Returns True if successful."""
        scenario_dir = self._scenario_dir(scenario_name)
        versions_dir = scenario_dir / _VERSIONS_DIR
        if not versions_dir.is_dir():
            return False
        versions = sorted(versions_dir.glob("mutations_v*.json"))
        if not versions:
            return False
        latest_archive = versions[-1]
        current = scenario_dir / _MUTATIONS_FILENAME
        current.write_text(latest_archive.read_text(encoding="utf-8"), encoding="utf-8")
        latest_archive.unlink()
        return True
