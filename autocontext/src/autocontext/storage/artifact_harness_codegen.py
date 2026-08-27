"""Harness validator codegen methods for ArtifactStore."""

from __future__ import annotations

import ast
import json
import logging
import re
import threading
from pathlib import Path
from typing import Any

from autocontext.security.confined_files import (
    ConfinedFileTooLarge,
    atomic_write_confined_text,
    list_confined_regular_files,
    read_confined_text,
    unlink_confined_file,
)
from autocontext.storage.scenario_paths import normalize_scenario_name_segment

logger = logging.getLogger(__name__)

MAX_HARNESS_SOURCE_BYTES = 1024 * 1024
MAX_HARNESS_CONTEXT_BYTES = 4 * 1024 * 1024
MAX_HARNESS_DIRECTORY_ENTRIES = 256
MAX_HARNESS_ARCHIVE_ENTRIES = 2048
MAX_HARNESS_METADATA_BYTES = 64 * 1024


class ConfinedHarnessVersionStore:
    """Version harness files without trusting path components or archive entries."""

    def __init__(self, knowledge_root: Path, scenario_name: str, *, max_versions: int) -> None:
        self._knowledge_root = knowledge_root
        self._parts = (normalize_scenario_name_segment(scenario_name), "harness")
        self._max_versions = max(0, max_versions)
        self._lock = threading.RLock()

    @staticmethod
    def _validate_name(name: str) -> str:
        if not re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]*\.py", name):
            raise ValueError(f"invalid harness filename: {name!r}")
        return name

    def _versions(self, name: str) -> list[tuple[int, str]]:
        safe_name = self._validate_name(name)
        try:
            filenames = list_confined_regular_files(
                self._knowledge_root,
                (*self._parts, "_archive"),
                suffix=".py",
                max_entries=MAX_HARNESS_ARCHIVE_ENTRIES,
            )
        except FileNotFoundError:
            return []
        named_pattern = re.compile(rf"v([0-9]+)_{re.escape(safe_name)}")
        legacy_pattern = re.compile(r"v([0-9]+)\.py")
        versions: list[tuple[int, str]] = []
        for filename in filenames:
            match = named_pattern.fullmatch(filename) or legacy_pattern.fullmatch(filename)
            if match is not None:
                versions.append((int(match.group(1)), filename))
        return sorted(versions)

    def write(self, name: str, content: str) -> None:
        """Atomically write a bounded harness and archive its current content."""
        safe_name = self._validate_name(name)
        if len(content.encode("utf-8")) > MAX_HARNESS_SOURCE_BYTES:
            raise ConfinedFileTooLarge("confined file exceeds its byte limit")
        with self._lock:
            existing = read_confined_text(
                self._knowledge_root,
                self._parts,
                safe_name,
                max_bytes=MAX_HARNESS_SOURCE_BYTES,
            )
            if existing is not None:
                versions = self._versions(safe_name)
                next_number = versions[-1][0] + 1 if versions else 1
                archive_name = f"v{next_number:04d}_{safe_name}"
                atomic_write_confined_text(
                    self._knowledge_root,
                    (*self._parts, "_archive"),
                    archive_name,
                    existing,
                    max_bytes=MAX_HARNESS_SOURCE_BYTES,
                )
                versions.append((next_number, archive_name))
                while len(versions) > self._max_versions:
                    _, oldest = versions.pop(0)
                    unlink_confined_file(
                        self._knowledge_root,
                        (*self._parts, "_archive"),
                        oldest,
                    )
            atomic_write_confined_text(
                self._knowledge_root,
                self._parts,
                safe_name,
                content,
                max_bytes=MAX_HARNESS_SOURCE_BYTES,
            )

    def read(self, name: str, default: str = "") -> str:
        """Read one current harness through the confined directory chain."""
        safe_name = self._validate_name(name)
        with self._lock:
            content = read_confined_text(
                self._knowledge_root,
                self._parts,
                safe_name,
                max_bytes=MAX_HARNESS_SOURCE_BYTES,
            )
        return default if content is None else content

    def rollback(self, name: str) -> bool:
        """Restore and remove the most recent confined archive for one harness."""
        safe_name = self._validate_name(name)
        with self._lock:
            versions = self._versions(safe_name)
            if not versions:
                return False
            _, latest = versions[-1]
            content = read_confined_text(
                self._knowledge_root,
                (*self._parts, "_archive"),
                latest,
                max_bytes=MAX_HARNESS_SOURCE_BYTES,
            )
            if content is None:
                return False
            atomic_write_confined_text(
                self._knowledge_root,
                self._parts,
                safe_name,
                content,
                max_bytes=MAX_HARNESS_SOURCE_BYTES,
            )
            unlink_confined_file(
                self._knowledge_root,
                (*self._parts, "_archive"),
                latest,
            )
            return True

    def version_count(self, name: str) -> int:
        """Return the number of confined archives belonging to one harness."""
        with self._lock:
            return len(self._versions(self._validate_name(name)))


class HarnessCodegenMethods:
    knowledge_root: Path
    _max_playbook_versions: int
    _confined_harness_stores: dict[str, ConfinedHarnessVersionStore]

    def _scenario_dir(self, scenario_name: str) -> Path:
        raise NotImplementedError

    def harness_dir(self, scenario_name: str) -> Path:
        """Return the harness directory: knowledge/<scenario>/harness/"""
        scenario = normalize_scenario_name_segment(scenario_name)
        return self.knowledge_root / scenario / "harness"

    def _harness_parts(self, scenario_name: str) -> tuple[str, str]:
        return (normalize_scenario_name_segment(scenario_name), "harness")

    def _harness_filenames(self, scenario_name: str) -> list[str]:
        try:
            filenames = list_confined_regular_files(
                self.knowledge_root,
                self._harness_parts(scenario_name),
                suffix=".py",
                max_entries=MAX_HARNESS_DIRECTORY_ENTRIES,
            )
        except FileNotFoundError:
            return []
        return [filename for filename in filenames if not filename.startswith("_")]

    @staticmethod
    def _validate_harness_name(name: str) -> str:
        """Validate harness module name and prevent path traversal."""
        candidate = name.strip()
        if not re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]*", candidate):
            raise ValueError(f"invalid harness name: {name!r}")
        return candidate

    def persist_harness(
        self,
        scenario_name: str,
        generation_index: int,
        specs: list[dict[str, Any]],
    ) -> list[str]:
        """AST-validate and write harness .py files, archiving old versions."""
        created: list[str] = []
        parts = self._harness_parts(scenario_name)
        for spec in specs:
            raw_name = str(spec.get("name", "")).strip()
            code = str(spec.get("code", "")).strip()
            description = str(spec.get("description", "")).strip()
            if not raw_name or not code:
                continue

            try:
                name = self._validate_harness_name(raw_name)
            except ValueError:
                logger.warning("skipping harness '%s': invalid name", raw_name)
                continue
            try:
                ast.parse(code)
            except SyntaxError:
                logger.warning("skipping harness '%s': syntax error in generated code", name)
                continue

            filename = f"{name}.py"
            wrapped = (
                '"""Harness validator generated by architect in generation '
                f"{generation_index}.\n\n{description}\n\"\"\"\n\n{code}\n"
            )
            if len(wrapped.encode("utf-8")) > MAX_HARNESS_SOURCE_BYTES:
                logger.warning("skipping harness '%s': source exceeds byte limit", name)
                continue
            existing = read_confined_text(
                self.knowledge_root,
                parts,
                filename,
                max_bytes=MAX_HARNESS_SOURCE_BYTES,
            )
            try:
                if existing is not None:
                    atomic_write_confined_text(
                        self.knowledge_root,
                        (*parts, "_archive"),
                        f"{name}_gen{generation_index}.py",
                        existing,
                        max_bytes=MAX_HARNESS_SOURCE_BYTES,
                    )
                atomic_write_confined_text(
                    self.knowledge_root,
                    parts,
                    filename,
                    wrapped,
                    max_bytes=MAX_HARNESS_SOURCE_BYTES,
                )
            except ConfinedFileTooLarge:
                logger.warning("skipping harness '%s': source exceeds byte limit", name)
                continue
            created.append(f"{filename} (updated)" if existing is not None else filename)
        return created

    def write_harness(self, scenario_name: str, name: str, source: str) -> Path:
        """Write a single harness file to knowledge/<scenario>/harness/<name>.py."""
        safe_name = self._validate_harness_name(name)
        filename = f"{safe_name}.py"
        atomic_write_confined_text(
            self.knowledge_root,
            self._harness_parts(scenario_name),
            filename,
            source,
            max_bytes=MAX_HARNESS_SOURCE_BYTES,
        )
        return self.harness_dir(scenario_name) / filename

    def read_harness(self, scenario_name: str, name: str) -> str | None:
        """Read a harness file by name, or None if not found."""
        safe_name = self._validate_harness_name(name)
        return read_confined_text(
            self.knowledge_root,
            self._harness_parts(scenario_name),
            f"{safe_name}.py",
            max_bytes=MAX_HARNESS_SOURCE_BYTES,
        )

    def list_harness(self, scenario_name: str) -> list[str]:
        """List all harness file names for a scenario (sorted, without .py extension)."""
        return [Path(filename).stem for filename in self._harness_filenames(scenario_name)]

    def read_harness_context(self, scenario_name: str) -> str:
        """Read harness validator files as markdown context for prompts."""
        parts = self._harness_parts(scenario_name)
        rendered: list[str] = []
        rendered_bytes = 0
        for filename in self._harness_filenames(scenario_name):
            content = read_confined_text(
                self.knowledge_root,
                parts,
                filename,
                max_bytes=MAX_HARNESS_SOURCE_BYTES,
            )
            if content is None:
                continue
            section = f"### {filename}\n```python\n{content}\n```"
            rendered_bytes += len(section.encode("utf-8"))
            if rendered_bytes > MAX_HARNESS_CONTEXT_BYTES:
                raise ConfinedFileTooLarge("harness context exceeds its byte limit")
            rendered.append(section)
        return "\n\n".join(rendered) if rendered else "No harness validators available."

    def _harness_store(self, scenario_name: str) -> ConfinedHarnessVersionStore:
        """Lazily create a descriptor-confined version store per scenario."""
        scenario = normalize_scenario_name_segment(scenario_name)
        if not hasattr(self, "_confined_harness_stores"):
            self._confined_harness_stores = {}
        if scenario not in self._confined_harness_stores:
            self._confined_harness_stores[scenario] = ConfinedHarnessVersionStore(
                self.knowledge_root,
                scenario,
                max_versions=self._max_playbook_versions,
            )
        return self._confined_harness_stores[scenario]

    def _harness_version_path(self, scenario_name: str) -> Path:
        return self.harness_dir(scenario_name) / "harness_version.json"

    def get_harness_version(self, scenario_name: str) -> dict[str, Any]:
        """Read bounded, confined harness version metadata."""
        content = read_confined_text(
            self.knowledge_root,
            self._harness_parts(scenario_name),
            "harness_version.json",
            max_bytes=MAX_HARNESS_METADATA_BYTES,
        )
        if content is None:
            return {}
        try:
            data = json.loads(content)
        except (TypeError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _update_harness_version(
        self,
        scenario_name: str,
        name: str,
        version: int,
        generation: int,
    ) -> None:
        versions = self.get_harness_version(scenario_name)
        versions[name] = {"version": version, "generation": generation}
        atomic_write_confined_text(
            self.knowledge_root,
            self._harness_parts(scenario_name),
            "harness_version.json",
            json.dumps(versions, indent=2, sort_keys=True),
            max_bytes=MAX_HARNESS_METADATA_BYTES,
        )

    def write_harness_versioned(
        self,
        scenario_name: str,
        name: str,
        source: str,
        generation: int,
    ) -> Path:
        """Write a harness with bounded, per-module confined archives."""
        normalized = self._validate_harness_name(name)
        store = self._harness_store(scenario_name)
        filename = f"{normalized}.py"
        store.write(filename, source)
        version = store.version_count(filename) + 1
        self._update_harness_version(scenario_name, normalized, version, generation)
        return self.harness_dir(scenario_name) / filename

    def rollback_harness(self, scenario_name: str, name: str) -> str | None:
        """Restore the most recent bounded archive for one harness."""
        normalized = self._validate_harness_name(name)
        store = self._harness_store(scenario_name)
        filename = f"{normalized}.py"
        if not store.rollback(filename):
            return None
        versions_info = self.get_harness_version(scenario_name)
        entry = versions_info.get(normalized)
        if isinstance(entry, dict) and isinstance(entry.get("version"), int) and entry["version"] > 1:
            entry["version"] -= 1
            stored_generation = entry.get("generation", 0)
            generation = stored_generation if isinstance(stored_generation, int) else 0
            self._update_harness_version(
                scenario_name,
                normalized,
                entry["version"],
                generation,
            )
        return store.read(filename)
