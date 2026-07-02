"""Skill note and operational lesson methods for ArtifactStore."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Protocol

from autocontext.storage.scenario_paths import normalize_scenario_name_segment


class SkillLessonHost(Protocol):
    claude_skills_path: Path
    skills_root: Path

    def _skill_dir(self, scenario_name: str) -> Path: ...
    def read_playbook(self, scenario_name: str) -> str: ...
    def sync_skills_to_claude(self) -> None: ...


class SkillLessonMethods:
    def persist_skill_note(self: SkillLessonHost, scenario_name: str, generation_index: int, decision: str, lessons: str) -> None:
        """Write a Claude Code Skill with playbook, lessons, and resource refs.

        The skill directory becomes the knowledge hub for this scenario:

        - ``SKILL.md`` — overview, lessons, and references (progressive disclosure)
        - ``playbook.md`` — current consolidated strategy playbook (bundled resource)

        Claude Code discovers the skill via YAML frontmatter and loads
        ``SKILL.md`` on demand.  When deeper context is needed it reads
        ``playbook.md`` (bundled) or follows references to the ``knowledge/``
        directory for analysis history, tools, and raw coach output.
        """
        scenario = normalize_scenario_name_segment(scenario_name)
        skill_dir = self._skill_dir(scenario)
        skill_path = skill_dir / "SKILL.md"

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

        if lessons and lessons.strip() not in ("", "No new lessons."):
            for line in lessons.strip().splitlines():
                stripped = line.strip()
                if not stripped:
                    continue
                bullet = stripped if stripped.startswith("- ") else f"- {stripped}"
                if bullet not in existing_bullets:
                    existing_bullets.append(bullet)

        kebab = scenario.replace("_", "-")
        title = scenario.replace("_", " ").title()
        desc = (
            f"Operational knowledge for the {scenario} scenario including "
            "strategy playbook, lessons learned, and resource references. "
            f"Use when generating, evaluating, coaching, or debugging "
            f"{scenario} strategies."
        )
        lessons_block = "\n".join(existing_bullets) if existing_bullets else "No lessons yet."

        skill_content = (
            f"---\nname: {kebab}-ops\ndescription: {desc}\n---\n\n"
            f"# {title} Operational Knowledge\n\n"
            "Accumulated knowledge from autocontext strategy evolution.\n\n"
            "## Operational Lessons\n\n"
            "Prescriptive rules derived from what worked and what failed:\n\n"
            f"{lessons_block}\n\n"
            "## Bundled Resources\n\n"
            "- **Strategy playbook**: See [playbook.md](playbook.md) for the "
            "current consolidated strategy guide (Strategy Updates, Prompt "
            "Optimizations, Next Generation Checklist)\n"
            f"- **Analysis history**: `knowledge/{scenario}/analysis/` "
            "— per-generation analysis markdown\n"
            f"- **Generated tools**: `knowledge/{scenario}/tools/` "
            "— architect-created Python tools\n"
            f"- **Coach history**: `knowledge/{scenario}/coach_history.md`"
            " — raw coach output across all generations\n"
            f"- **Architect changelog**: "
            f"`knowledge/{scenario}/architect/changelog.md`"
            " — infrastructure and tooling changes\n"
        )

        skill_dir.mkdir(parents=True, exist_ok=True)
        skill_path.write_text(skill_content, encoding="utf-8")

        playbook_content = self.read_playbook(scenario)
        (skill_dir / "playbook.md").write_text(
            playbook_content.strip() + "\n",
            encoding="utf-8",
        )

        self.sync_skills_to_claude()

    def read_skill_lessons_raw(self: SkillLessonHost, scenario_name: str) -> list[str]:
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

    def replace_skill_lessons(self: SkillLessonHost, scenario_name: str, lessons: list[str]) -> None:
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

    def sync_skills_to_claude(self: SkillLessonHost) -> None:
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
