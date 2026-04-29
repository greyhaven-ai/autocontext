"""Pi-compatible package export for autocontext strategy packages."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from autocontext import __version__

if TYPE_CHECKING:
    from autocontext.knowledge.package import StrategyPackage

_PI_TOOL_NAMES = (
    "autocontext_solve_scenario",
    "autocontext_export_package",
    "autocontext_import_package",
    "autocontext_run_status",
)


@dataclass(frozen=True, slots=True)
class PiPackage:
    """In-memory representation of a local Pi-installable package directory."""

    package_dir_name: str
    files: dict[str, str]


@dataclass(frozen=True, slots=True)
class WrittenPiPackage:
    """Filesystem result for a written Pi package."""

    output_dir: Path
    files: tuple[Path, ...]


def build_pi_package(package: StrategyPackage) -> PiPackage:
    """Build a Pi-compatible local package from a strategy package."""
    scenario_slug = _slug(package.scenario_name)
    skill_path = f"skills/{scenario_slug}-knowledge/SKILL.md"
    prompt_path = f"prompts/{scenario_slug}.md"
    strategy_path = "autocontext.package.json"

    files = {
        "README.md": _render_readme(package),
        strategy_path: package.to_json(),
        "package.json": _render_package_json(package, skill_path=skill_path, prompt_path=prompt_path),
        prompt_path: _render_prompt(package),
        skill_path: package.to_skill_package().to_skill_markdown(),
    }
    return PiPackage(package_dir_name=f"{scenario_slug}-pi-package", files=_sort_files(files))


def write_pi_package(package: PiPackage, output_dir: Path) -> WrittenPiPackage:
    """Write a Pi package directory and return the files written."""
    output_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for relative_path, content in package.files.items():
        path = output_dir / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content.rstrip() + "\n", encoding="utf-8")
        written.append(path)
    return WrittenPiPackage(output_dir=output_dir, files=tuple(written))


def default_pi_package_output_dir(scenario_name: str) -> Path:
    """Return the default local package directory for a scenario."""
    return Path(f"{_slug(scenario_name)}-pi-package")


def _sort_files(files: dict[str, str]) -> dict[str, str]:
    return {key: files[key] for key in sorted(files)}


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "autocontext"


def _npm_package_name(scenario_name: str) -> str:
    return f"autocontext-{_slug(scenario_name)}-pi-package"


def _description(package: StrategyPackage) -> str:
    description = package.description.strip()
    return description or f"Autocontext package for {package.display_name or package.scenario_name}"


def _render_package_json(package: StrategyPackage, *, skill_path: str, prompt_path: str) -> str:
    manifest = {
        "name": _npm_package_name(package.scenario_name),
        "version": _package_version(package),
        "private": True,
        "description": _description(package)[:200],
        "files": [
            "README.md",
            "autocontext.package.json",
            "prompts",
            "skills",
        ],
        "pi": {
            "skills": [skill_path],
            "prompts": [prompt_path],
            "extensions": [],
            "themes": [],
        },
        "autocontext": {
            "format": "pi-package",
            "scenario_name": package.scenario_name,
            "strategy_package": "autocontext.package.json",
            "tools": list(_PI_TOOL_NAMES),
        },
    }
    return json.dumps(manifest, indent=2, sort_keys=True)


def _package_version(package: StrategyPackage) -> str:
    version = package.metadata.mts_version.strip()
    return version or __version__


def _render_prompt(package: StrategyPackage) -> str:
    title = package.display_name or package.scenario_name.replace("_", " ").title()
    parts = [
        f"# {title}",
        "",
        _description(package),
        "",
        "Use this Pi package as a lean autocontext operating context.",
        "",
        "## Autocontext Tools",
        "",
    ]
    parts.extend(f"- `{tool}`" for tool in _PI_TOOL_NAMES)
    if package.task_prompt:
        parts.extend(["", "## Task", "", package.task_prompt])
    if package.judge_rubric:
        parts.extend(["", "## Evaluation Criteria", "", package.judge_rubric])
    if package.playbook:
        parts.extend(["", "## Playbook", "", package.playbook])
    if package.lessons:
        parts.extend(["", "## Lessons", ""])
        parts.extend(f"- {lesson}" for lesson in package.lessons)
    if package.best_strategy:
        parts.extend([
            "",
            "## Best Known Strategy",
            "",
            "```json",
            json.dumps(package.best_strategy, indent=2, sort_keys=True),
            "```",
        ])
    return "\n".join(parts)


def _render_readme(package: StrategyPackage) -> str:
    title = package.display_name or package.scenario_name.replace("_", " ").title()
    scenario_slug = _slug(package.scenario_name)
    return "\n".join([
        f"# {title} Pi Package",
        "",
        _description(package),
        "",
        "This package was generated by autocontext for local Pi package installation.",
        "",
        "## Contents",
        "",
        f"- `skills/{scenario_slug}-knowledge/SKILL.md`",
        f"- `prompts/{scenario_slug}.md`",
        "- `autocontext.package.json`",
        "",
        "The strategy package can be re-imported with `autocontext_import_package`.",
    ])
