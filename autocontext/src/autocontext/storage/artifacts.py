from __future__ import annotations

import ast
import json
import logging
import os
import re
from collections.abc import Callable
from pathlib import Path

from autocontext.harness.storage.versioned_store import VersionedFileStore
from autocontext.storage.buffered_writer import BufferedWriter

LOGGER = logging.getLogger(__name__)

EMPTY_PLAYBOOK_SENTINEL = "No playbook yet. Start from scenario rules and observation."


class ArtifactStore:
    def __init__(
        self,
        runs_root: Path,
        knowledge_root: Path,
        skills_root: Path,
        claude_skills_path: Path,
        max_playbook_versions: int = 5,
        enable_buffered_writes: bool = False,
    ):
        self.runs_root = runs_root
        self.knowledge_root = knowledge_root
        self.skills_root = skills_root
        self.claude_skills_path = claude_skills_path
        self._max_playbook_versions = max_playbook_versions
        self._playbook_stores: dict[str, VersionedFileStore] = {}
        self._writer: BufferedWriter | None = None
        if enable_buffered_writes:
            self._writer = BufferedWriter()
            self._writer.start()

    def _playbook_store(self, scenario_name: str) -> VersionedFileStore:
        """Lazily create a per-scenario VersionedFileStore with legacy naming."""
        if scenario_name not in self._playbook_stores:
            self._playbook_stores[scenario_name] = VersionedFileStore(
                root=self.knowledge_root / scenario_name,
                max_versions=self._max_playbook_versions,
                versions_dir_name="playbook_versions",
                version_prefix="playbook_v",
                version_suffix=".md",
            )
        return self._playbook_stores[scenario_name]

    def generation_dir(self, run_id: str, generation_index: int) -> Path:
        return self.runs_root / run_id / "generations" / f"gen_{generation_index}"

    def write_json(self, path: Path, payload: dict[str, object]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    def write_markdown(self, path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content.strip() + "\n", encoding="utf-8")

    def append_markdown(self, path: Path, content: str, heading: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        chunk = f"\n## {heading}\n\n{content.strip()}\n"
        if path.exists():
            with path.open("a", encoding="utf-8") as handle:
                handle.write(chunk)
            return
        path.write_text(chunk.lstrip("\n"), encoding="utf-8")

    def flush_writes(self) -> None:
        """Block until all buffered writes are flushed."""
        if self._writer is not None:
            self._writer.flush()

    def shutdown_writer(self) -> None:
        """Flush and stop the background writer thread."""
        if self._writer is not None:
            self._writer.shutdown()
            self._writer = None

    def buffered_write_json(self, path: Path, payload: dict[str, object]) -> None:
        """Write JSON via buffer if available, otherwise synchronous."""
        if self._writer is not None:
            self._writer.write_json(path, payload)
        else:
            self.write_json(path, payload)

    def buffered_write_markdown(self, path: Path, content: str) -> None:
        """Write markdown via buffer if available, otherwise synchronous."""
        if self._writer is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            self._writer.write_text(path, content.strip() + "\n")
        else:
            self.write_markdown(path, content)

    def buffered_append_markdown(self, path: Path, content: str, heading: str) -> None:
        """Append markdown via buffer if available, otherwise synchronous."""
        if self._writer is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            chunk = f"\n## {heading}\n\n{content.strip()}\n"
            self._writer.append_text(path, chunk)
        else:
            self.append_markdown(path, content, heading)

    def read_playbook(self, scenario_name: str) -> str:
        content = self._playbook_store(scenario_name).read("playbook.md")
        if not content:
            return EMPTY_PLAYBOOK_SENTINEL
        return content

    def write_playbook(self, scenario_name: str, content: str) -> None:
        """Overwrite the playbook, archiving current version first."""
        # Ensure parent directory exists (VersionedFileStore.write handles the file,
        # but the scenario directory itself may not exist yet).
        (self.knowledge_root / scenario_name).mkdir(parents=True, exist_ok=True)
        self._playbook_store(scenario_name).write("playbook.md", content.strip() + "\n")

    def append_coach_history(self, scenario_name: str, generation_index: int, raw_content: str) -> None:
        """Append raw coach output to history file for audit trail."""
        history_path = self.knowledge_root / scenario_name / "coach_history.md"
        self.append_markdown(history_path, raw_content, heading=f"generation_{generation_index}")

    def _skill_dir(self, scenario_name: str) -> Path:
        """Skill directory: skills/<kebab-scenario>-ops/"""
        return self.skills_root / f"{scenario_name.replace('_', '-')}-ops"

    def read_skills(self, scenario_name: str) -> str:
        """Read operational lessons for injection into AutoContext agent prompts.

        Extracts only the ``## Operational Lessons`` section from SKILL.md.
        The playbook is already injected separately via ``current_playbook``
        in the prompt bundle, so we avoid duplication here.  Claude Code
        reads the full SKILL.md (with bundled resources) on its own.
        """
        skill_path = self._skill_dir(scenario_name) / "SKILL.md"
        if not skill_path.exists():
            return ""
        content = skill_path.read_text(encoding="utf-8")
        marker = "## Operational Lessons"
        start = content.find(marker)
        if start == -1:
            return ""
        after = content[start + len(marker):]
        next_heading = after.find("\n## ")
        if next_heading != -1:
            return after[:next_heading].strip()
        return after.strip()

    def write_hints(self, scenario_name: str, content: str) -> None:
        """Persist coach hints so they survive run restarts."""
        self.write_markdown(self.knowledge_root / scenario_name / "hints.md", content)

    def read_hints(self, scenario_name: str) -> str:
        """Read persisted hints, or empty string if none."""
        path = self.knowledge_root / scenario_name / "hints.md"
        return path.read_text(encoding="utf-8") if path.exists() else ""

    def read_dead_ends(self, scenario_name: str) -> str:
        """Read dead-end registry, or empty string if none."""
        path = self.knowledge_root / scenario_name / "dead_ends.md"
        return path.read_text(encoding="utf-8") if path.exists() else ""

    def append_dead_end(self, scenario_name: str, entry: str) -> None:
        """Append a dead-end entry to the registry file."""
        path = self.knowledge_root / scenario_name / "dead_ends.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        chunk = f"\n### Dead End\n\n{entry}\n"
        if path.exists():
            with path.open("a", encoding="utf-8") as handle:
                handle.write(chunk)
        else:
            path.write_text(chunk.lstrip("\n"), encoding="utf-8")

    def replace_dead_ends(self, scenario_name: str, content: str) -> None:
        """Overwrite the entire dead_ends.md file (for curator consolidation)."""
        path = self.knowledge_root / scenario_name / "dead_ends.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def read_research_protocol(self, scenario_name: str) -> str:
        """Read research protocol, or empty string if none."""
        path = self.knowledge_root / scenario_name / "research_protocol.md"
        return path.read_text(encoding="utf-8") if path.exists() else ""

    def write_research_protocol(self, scenario_name: str, content: str) -> None:
        """Write research protocol."""
        path = self.knowledge_root / scenario_name / "research_protocol.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def write_progress(self, scenario_name: str, snapshot_dict: dict[str, object]) -> None:
        """Write progress snapshot JSON."""
        path = self.knowledge_root / scenario_name / "progress.json"
        self.write_json(path, snapshot_dict)

    def read_progress(self, scenario_name: str) -> dict[str, object] | None:
        """Read progress snapshot, or None if missing."""
        path = self.knowledge_root / scenario_name / "progress.json"
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))  # type: ignore[no-any-return]

    def read_latest_advance_analysis(self, scenario_name: str, current_gen: int) -> str:
        """Read the most recent analysis from a generation before current_gen."""
        analysis_dir = self.knowledge_root / scenario_name / "analysis"
        if not analysis_dir.exists():
            return ""
        candidates = sorted(analysis_dir.glob("gen_*.md"), reverse=True)
        for path in candidates:
            try:
                num = int(path.stem.split("_")[1])
            except (IndexError, ValueError):
                continue
            if num < current_gen:
                return path.read_text(encoding="utf-8")
        return ""

    def harness_dir(self, scenario_name: str) -> Path:
        """Return the harness directory: knowledge/<scenario>/harness/"""
        return self.knowledge_root / scenario_name / "harness"

    @staticmethod
    def _validate_harness_name(name: str) -> str:
        """Validate harness module name and prevent path traversal."""
        candidate = name.strip()
        if not re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]*", candidate):
            raise ValueError(f"invalid harness name: {name!r}")
        return candidate

    @staticmethod
    def _list_python_modules(directory: Path) -> list[str]:
        """List top-level Python modules, excluding private helper files."""
        if not directory.exists():
            return []
        return sorted(
            path.stem
            for path in directory.glob("*.py")
            if path.is_file() and not path.name.startswith("_")
        )

    @staticmethod
    def _render_python_context(
        directory: Path,
        *,
        empty_message: str,
        name_prefix: str = "",
    ) -> str:
        lines: list[str] = []
        if directory.exists():
            for module_file in sorted(directory.glob("*.py")):
                if not module_file.is_file() or module_file.name.startswith("_"):
                    continue
                content = module_file.read_text(encoding="utf-8")
                lines.append(f"### {name_prefix}{module_file.name}\n```python\n{content}\n```")
        return "\n\n".join(lines) if lines else empty_message

    @staticmethod
    def _wrap_generated_module(header: str, description: str, code: str) -> str:
        return f'"""{header}\n\n{description}\n"""\n\n{code}\n'

    def _persist_generated_modules(
        self,
        directory: Path,
        generation_index: int,
        specs: list[dict[str, object]],
        *,
        kind: str,
        header_template: str,
        name_validator: Callable[[str], str] | None = None,
    ) -> list[str]:
        created: list[str] = []
        if not specs:
            return created

        directory.mkdir(parents=True, exist_ok=True)
        archive_dir = directory / "_archive"
        for spec in specs:
            raw_name = str(spec.get("name", "")).strip()
            code = str(spec.get("code", "")).strip()
            description = str(spec.get("description", "")).strip()
            if not raw_name or not code:
                continue

            try:
                name = name_validator(raw_name) if name_validator is not None else raw_name
            except ValueError:
                LOGGER.warning("skipping %s '%s': invalid name", kind, raw_name)
                continue

            try:
                ast.parse(code)
            except SyntaxError:
                LOGGER.warning("skipping %s '%s': syntax error in generated code", kind, name)
                continue

            target = directory / f"{name}.py"
            is_update = target.exists()
            if is_update:
                archive_dir.mkdir(parents=True, exist_ok=True)
                archive_path = archive_dir / f"{name}_gen{generation_index}.py"
                archive_path.write_text(target.read_text(encoding="utf-8"), encoding="utf-8")

            wrapped = self._wrap_generated_module(
                header_template.format(generation_index=generation_index),
                description,
                code,
            )
            target.write_text(wrapped, encoding="utf-8")
            created.append(f"{target.name} (updated)" if is_update else target.name)

        return created

    def persist_harness(
        self, scenario_name: str, generation_index: int, specs: list[dict[str, object]],
    ) -> list[str]:
        """AST-validate and write harness .py files, archiving old versions."""
        return self._persist_generated_modules(
            self.harness_dir(scenario_name),
            generation_index,
            specs,
            kind="harness",
            header_template="Harness validator generated by architect in generation {generation_index}.",
            name_validator=self._validate_harness_name,
        )

    def write_harness(self, scenario_name: str, name: str, source: str) -> Path:
        """Write a single harness file to knowledge/<scenario>/harness/<name>.py."""
        safe_name = self._validate_harness_name(name)
        h_dir = self.harness_dir(scenario_name)
        h_dir.mkdir(parents=True, exist_ok=True)
        target = h_dir / f"{safe_name}.py"
        target.write_text(source, encoding="utf-8")
        return target

    def read_harness(self, scenario_name: str, name: str) -> str | None:
        """Read a harness file by name, or None if not found."""
        safe_name = self._validate_harness_name(name)
        target = self.harness_dir(scenario_name) / f"{safe_name}.py"
        if not target.exists():
            return None
        return target.read_text(encoding="utf-8")

    def list_harness(self, scenario_name: str) -> list[str]:
        """List all harness file names for a scenario (sorted, without .py extension)."""
        return self._list_python_modules(self.harness_dir(scenario_name))

    def read_harness_context(self, scenario_name: str) -> str:
        """Read harness validator files as markdown context for prompts."""
        return self._render_python_context(
            self.harness_dir(scenario_name),
            empty_message="No harness validators available.",
        )

    def tools_dir(self, scenario_name: str) -> Path:
        return self.knowledge_root / scenario_name / "tools"

    def shared_tools_dir(self) -> Path:
        return self.knowledge_root / "_shared" / "tools"

    def persist_tools(self, scenario_name: str, generation_index: int, tools: list[dict[str, object]]) -> list[str]:
        return self._persist_generated_modules(
            self.tools_dir(scenario_name),
            generation_index,
            tools,
            kind="tool",
            header_template="Generated by architect in generation {generation_index}.",
        )

    def read_tool_context(self, scenario_name: str) -> str:
        sections: list[str] = []
        tool_context = self._render_python_context(
            self.tools_dir(scenario_name),
            empty_message="",
        )
        if tool_context:
            sections.append(tool_context)

        shared_context = self._render_python_context(
            self.shared_tools_dir(),
            empty_message="",
            name_prefix="[shared] ",
        )
        if shared_context:
            sections.append(shared_context)

        return "\n\n".join(sections) if sections else "No generated tools available."

    def persist_generation(
        self,
        run_id: str,
        generation_index: int,
        metrics: dict[str, object],
        replay_payload: dict[str, object],
        analysis_md: str,
        coach_md: str,
        architect_md: str,
        scenario_name: str,
        coach_playbook: str = "",
    ) -> None:
        gen_dir = self.generation_dir(run_id, generation_index)
        # Non-critical writes — buffer if available
        self.buffered_write_json(gen_dir / "metrics.json", metrics)
        self.buffered_write_json(gen_dir / "replays" / f"{scenario_name}_{generation_index}.json", replay_payload)
        analysis_path = self.knowledge_root / scenario_name / "analysis" / f"gen_{generation_index}.md"
        self.buffered_write_markdown(analysis_path, analysis_md)
        self.buffered_append_markdown(
            self.knowledge_root / scenario_name / "coach_history.md",
            coach_md,
            heading=f"generation_{generation_index}",
        )
        # Critical write — always synchronous (versioned)
        if coach_playbook:
            self.write_playbook(scenario_name, coach_playbook)
        self.buffered_append_markdown(
            self.knowledge_root / scenario_name / "architect" / "changelog.md",
            architect_md,
            heading=f"generation_{generation_index}",
        )

    def persist_skill_note(self, scenario_name: str, generation_index: int, decision: str, lessons: str) -> None:
        """Write a Claude Code Skill with playbook, lessons, and resource refs.

        The skill directory becomes the knowledge hub for this scenario:

        - ``SKILL.md`` — overview, lessons, and references (progressive disclosure)
        - ``playbook.md`` — current consolidated strategy playbook (bundled resource)

        Claude Code discovers the skill via YAML frontmatter and loads
        ``SKILL.md`` on demand.  When deeper context is needed it reads
        ``playbook.md`` (bundled) or follows references to the ``knowledge/``
        directory for analysis history, tools, and raw coach output.
        """
        skill_dir = self._skill_dir(scenario_name)
        skill_path = skill_dir / "SKILL.md"

        # --- Collect existing lesson bullets ----------------------------------
        existing_bullets: list[str] = []
        if skill_path.exists():
            in_lessons = False
            for line in skill_path.read_text(encoding="utf-8").splitlines():
                if line.startswith("## Operational Lessons"):
                    in_lessons = True
                    continue
                if in_lessons and line.startswith("## "):
                    break
                if in_lessons and line.startswith("- "):
                    existing_bullets.append(line)

        # --- Add new lessons (deduplicated) -----------------------------------
        if lessons and lessons.strip() not in ("", "No new lessons."):
            for line in lessons.strip().splitlines():
                stripped = line.strip()
                if not stripped:
                    continue
                bullet = stripped if stripped.startswith("- ") else f"- {stripped}"
                if bullet not in existing_bullets:
                    existing_bullets.append(bullet)

        # --- Build SKILL.md ---------------------------------------------------
        kebab = scenario_name.replace("_", "-")
        title = scenario_name.replace("_", " ").title()
        desc = (
            f"Operational knowledge for the {scenario_name} scenario including "
            "strategy playbook, lessons learned, and resource references. "
            f"Use when generating, evaluating, coaching, or debugging "
            f"{scenario_name} strategies."
        )
        lessons_block = "\n".join(existing_bullets) if existing_bullets else "No lessons yet."

        skill_content = (
            f"---\nname: {kebab}-ops\ndescription: {desc}\n---\n\n"
            f"# {title} Operational Knowledge\n\n"
            "Accumulated knowledge from AutoContext strategy evolution.\n\n"
            "## Operational Lessons\n\n"
            "Prescriptive rules derived from what worked and what failed:\n\n"
            f"{lessons_block}\n\n"
            "## Bundled Resources\n\n"
            "- **Strategy playbook**: See [playbook.md](playbook.md) for the "
            "current consolidated strategy guide (Strategy Updates, Prompt "
            "Optimizations, Next Generation Checklist)\n"
            f"- **Analysis history**: `knowledge/{scenario_name}/analysis/` "
            "— per-generation analysis markdown\n"
            f"- **Generated tools**: `knowledge/{scenario_name}/tools/` "
            "— architect-created Python tools\n"
            f"- **Coach history**: `knowledge/{scenario_name}/coach_history.md`"
            " — raw coach output across all generations\n"
            f"- **Architect changelog**: "
            f"`knowledge/{scenario_name}/architect/changelog.md`"
            " — infrastructure and tooling changes\n"
        )

        skill_dir.mkdir(parents=True, exist_ok=True)
        skill_path.write_text(skill_content, encoding="utf-8")

        # --- Bundle the current playbook into the skill directory -------------
        playbook_content = self.read_playbook(scenario_name)
        (skill_dir / "playbook.md").write_text(
            playbook_content.strip() + "\n", encoding="utf-8",
        )

        self.sync_skills_to_claude()

    def snapshot_knowledge(self, scenario_name: str, run_id: str) -> str:
        """Copy playbook + skills + hints to snapshots/<run_id>/. Returns playbook hash."""
        import hashlib

        snapshot_dir = self.knowledge_root / scenario_name / "snapshots" / run_id
        snapshot_dir.mkdir(parents=True, exist_ok=True)

        playbook_content = ""
        playbook_path = self.knowledge_root / scenario_name / "playbook.md"
        if playbook_path.exists():
            playbook_content = playbook_path.read_text(encoding="utf-8")
            (snapshot_dir / "playbook.md").write_text(playbook_content, encoding="utf-8")

        hints_path = self.knowledge_root / scenario_name / "hints.md"
        if hints_path.exists():
            (snapshot_dir / "hints.md").write_text(
                hints_path.read_text(encoding="utf-8"), encoding="utf-8"
            )

        skill_dir = self._skill_dir(scenario_name)
        skill_path = skill_dir / "SKILL.md"
        if skill_path.exists():
            (snapshot_dir / "SKILL.md").write_text(
                skill_path.read_text(encoding="utf-8"), encoding="utf-8"
            )

        # Snapshot harness files
        h_dir = self.harness_dir(scenario_name)
        if h_dir.exists():
            harness_snapshot = snapshot_dir / "harness"
            harness_snapshot.mkdir(parents=True, exist_ok=True)
            for py_file in h_dir.glob("*.py"):
                if py_file.is_file():
                    (harness_snapshot / py_file.name).write_text(
                        py_file.read_text(encoding="utf-8"), encoding="utf-8",
                    )

        return hashlib.sha256(playbook_content.encode("utf-8")).hexdigest()[:16]

    def restore_knowledge_snapshot(self, scenario_name: str, source_run_id: str) -> bool:
        """Restore knowledge from a snapshot. Returns True if restored."""
        snapshot_dir = self.knowledge_root / scenario_name / "snapshots" / source_run_id
        if not snapshot_dir.exists():
            return False

        restored = False
        pb_snapshot = snapshot_dir / "playbook.md"
        if pb_snapshot.exists():
            self.write_playbook(scenario_name, pb_snapshot.read_text(encoding="utf-8"))
            restored = True

        hints_snapshot = snapshot_dir / "hints.md"
        if hints_snapshot.exists():
            self.write_markdown(
                self.knowledge_root / scenario_name / "hints.md",
                hints_snapshot.read_text(encoding="utf-8"),
            )
            restored = True

        skill_snapshot = snapshot_dir / "SKILL.md"
        if skill_snapshot.exists():
            skill_dir = self._skill_dir(scenario_name)
            skill_dir.mkdir(parents=True, exist_ok=True)
            (skill_dir / "SKILL.md").write_text(
                skill_snapshot.read_text(encoding="utf-8"), encoding="utf-8"
            )
            restored = True

        # Restore harness files from snapshot
        harness_snapshot = snapshot_dir / "harness"
        if harness_snapshot.exists():
            h_dir = self.harness_dir(scenario_name)
            h_dir.mkdir(parents=True, exist_ok=True)
            for py_file in harness_snapshot.glob("*.py"):
                if py_file.is_file():
                    (h_dir / py_file.name).write_text(
                        py_file.read_text(encoding="utf-8"), encoding="utf-8",
                    )
            restored = True

        return restored

    def read_skill_lessons_raw(self, scenario_name: str) -> list[str]:
        """Return list of lesson bullet strings from SKILL.md."""
        skill_path = self._skill_dir(scenario_name) / "SKILL.md"
        if not skill_path.exists():
            return []
        bullets: list[str] = []
        in_lessons = False
        for line in skill_path.read_text(encoding="utf-8").splitlines():
            if line.startswith("## Operational Lessons"):
                in_lessons = True
                continue
            if in_lessons and line.startswith("## "):
                break
            if in_lessons and line.startswith("- "):
                bullets.append(line)
        return bullets

    def replace_skill_lessons(self, scenario_name: str, lessons: list[str]) -> None:
        """Replace the Operational Lessons section in SKILL.md with given bullets."""
        skill_path = self._skill_dir(scenario_name) / "SKILL.md"
        if not skill_path.exists():
            return
        content = skill_path.read_text(encoding="utf-8")
        lines = content.splitlines()
        result: list[str] = []
        in_lessons = False
        lessons_written = False
        for line in lines:
            if line.startswith("## Operational Lessons"):
                result.append(line)
                result.append("")
                result.append("Prescriptive rules derived from what worked and what failed:")
                result.append("")
                for bullet in lessons:
                    result.append(bullet if bullet.startswith("- ") else f"- {bullet}")
                in_lessons = True
                lessons_written = True
                continue
            if in_lessons:
                if line.startswith("## "):
                    in_lessons = False
                    result.append("")
                    result.append(line)
                # Skip old lesson lines
                continue
            result.append(line)
        if lessons_written:
            skill_path.write_text("\n".join(result) + "\n", encoding="utf-8")

    def sync_skills_to_claude(self) -> None:
        """Symlink skill directories into .claude/skills/ for Claude Code discovery."""
        self.claude_skills_path.mkdir(parents=True, exist_ok=True)
        if not self.skills_root.exists():
            return
        for entry in self.skills_root.iterdir():
            if not entry.is_dir() or not (entry / "SKILL.md").exists():
                continue
            link = self.claude_skills_path / entry.name
            if link.is_symlink():
                if link.resolve() == entry.resolve():
                    continue
                link.unlink()
            elif link.exists():
                continue  # Real file/dir exists, don't overwrite
            os.symlink(entry.resolve(), link)

    def write_session_report(self, scenario_name: str, run_id: str, content: str) -> None:
        """Write a session report for a completed run."""
        path = self.knowledge_root / scenario_name / "reports" / f"{run_id}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def read_latest_session_reports(self, scenario_name: str, max_reports: int = 2) -> str:
        """Read the most recent session reports, concatenated."""
        reports_dir = self.knowledge_root / scenario_name / "reports"
        if not reports_dir.exists():
            return ""
        report_files = sorted(reports_dir.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
        reports = []
        for path in report_files[:max_reports]:
            reports.append(path.read_text(encoding="utf-8"))
        return "\n\n---\n\n".join(reports)

    # --- Harness versioning ---------------------------------------------------

    def _harness_store(self, scenario_name: str) -> VersionedFileStore:
        """Lazily create a per-scenario VersionedFileStore for harness files."""
        key = f"harness:{scenario_name}"
        if key not in self._playbook_stores:
            self._playbook_stores[key] = VersionedFileStore(
                root=self.harness_dir(scenario_name),
                max_versions=self._max_playbook_versions,
                versions_dir_name="_archive",
                version_prefix="v",
                version_suffix=".py",
            )
        return self._playbook_stores[key]

    def _harness_version_path(self, scenario_name: str) -> Path:
        return self.harness_dir(scenario_name) / "harness_version.json"

    def get_harness_version(self, scenario_name: str) -> dict[str, object]:
        """Read harness_version.json — tracks current version per function."""
        path = self._harness_version_path(scenario_name)
        if not path.exists():
            return {}
        return json.loads(path.read_text(encoding="utf-8"))  # type: ignore[no-any-return]

    def _update_harness_version(
        self, scenario_name: str, name: str, version: int, generation: int,
    ) -> None:
        versions = self.get_harness_version(scenario_name)
        versions[name] = {"version": version, "generation": generation}
        path = self._harness_version_path(scenario_name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(versions, indent=2, sort_keys=True), encoding="utf-8")

    def write_harness_versioned(
        self, scenario_name: str, name: str, source: str, generation: int,
    ) -> Path:
        """Write a harness file with version tracking, archiving the previous version."""
        normalized = self._validate_harness_name(name)
        store = self._harness_store(scenario_name)
        filename = f"{normalized}.py"
        store.write(filename, source)
        version = store.version_count(filename) + 1
        self._update_harness_version(scenario_name, normalized, version, generation)
        return self.harness_dir(scenario_name) / filename

    def rollback_harness(self, scenario_name: str, name: str) -> str | None:
        """Restore previous version of a harness file from archive.

        Returns the restored content, or None if no archived version exists.
        """
        normalized = self._validate_harness_name(name)
        store = self._harness_store(scenario_name)
        filename = f"{normalized}.py"
        if not store.rollback(filename):
            return None
        # Update version metadata
        versions_info = self.get_harness_version(scenario_name)
        entry = versions_info.get(normalized)
        if isinstance(entry, dict) and isinstance(entry.get("version"), int) and entry["version"] > 1:
            entry["version"] -= 1
            self._update_harness_version(
                scenario_name, normalized, entry["version"], entry.get("generation", 0),  # type: ignore[arg-type]
            )
        return store.read(filename)

    def read_tuning(self, scenario_name: str) -> str:
        """Read tuning config JSON, or empty string if none."""
        path = self.knowledge_root / scenario_name / "tuning.json"
        return path.read_text(encoding="utf-8") if path.exists() else ""

    def write_tuning(self, scenario_name: str, content: str) -> None:
        """Write tuning config JSON."""
        path = self.knowledge_root / scenario_name / "tuning.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def read_notebook(self, session_id: str) -> dict[str, object] | None:
        """Read notebook JSON from runs/sessions/<session_id>/notebook.json."""
        path = self.runs_root / "sessions" / session_id / "notebook.json"
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))  # type: ignore[no-any-return]

    def write_notebook(self, session_id: str, notebook: dict[str, object]) -> None:
        """Write notebook JSON to runs/sessions/<session_id>/notebook.json."""
        path = self.runs_root / "sessions" / session_id / "notebook.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(notebook, indent=2), encoding="utf-8")

    def delete_notebook(self, session_id: str) -> None:
        """Delete the file-backed notebook artifact if it exists."""
        path = self.runs_root / "sessions" / session_id / "notebook.json"
        if path.exists():
            path.unlink()

    # --- Pi session artifacts (AC-224) ----------------------------------------

    def persist_pi_session(self, run_id: str, generation: int, trace: object, *, role: str = "") -> Path:
        """Persist a PiExecutionTrace to the generation directory.

        Writes:
        - pi_session.json / pi_{role}_session.json — serialized trace
        - pi_output.txt / pi_{role}_output.txt  — raw output for replay

        Args:
            run_id: The run identifier.
            generation: Generation index.
            trace: A PiExecutionTrace instance (duck-typed to avoid circular import).

        Returns:
            Path to the pi_session.json file.
        """
        gen_dir = self.generation_dir(run_id, generation)
        trace_dict: dict[str, object] = trace.to_dict()  # type: ignore[attr-defined]
        prefix = f"pi_{role}" if role else "pi"
        session_path = gen_dir / f"{prefix}_session.json"
        self.write_json(session_path, trace_dict)
        output_path = gen_dir / f"{prefix}_output.txt"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        raw_output = str(trace_dict.get("raw_output", ""))
        output_path.write_text(raw_output, encoding="utf-8")
        return session_path

    def read_pi_session(self, run_id: str, generation: int, *, role: str = "") -> dict[str, object] | None:
        """Read a persisted Pi session trace, or None if missing."""
        prefix = f"pi_{role}" if role else "pi"
        session_path = self.generation_dir(run_id, generation) / f"{prefix}_session.json"
        if not session_path.exists():
            return None
        return json.loads(session_path.read_text(encoding="utf-8"))  # type: ignore[no-any-return]
