"""Helpers for resolving per-scenario filesystem paths."""

from __future__ import annotations

from pathlib import Path, PurePosixPath, PureWindowsPath


def normalize_scenario_name_segment(scenario_name: str) -> str:
    """Return a stripped single path segment or raise for unsafe names."""
    normalized = scenario_name.strip()
    if not normalized:
        raise ValueError("scenario_name is required")
    if "/" in normalized or "\\" in normalized:
        raise ValueError(f"scenario_name must be a single path segment: {scenario_name!r}")

    for path_cls in (PurePosixPath, PureWindowsPath):
        candidate = path_cls(normalized)
        if candidate.is_absolute() or len(candidate.parts) != 1 or candidate.parts[0] in {".", ".."}:
            raise ValueError(f"scenario_name must be a single path segment: {scenario_name!r}")
    return normalized


def resolve_scenario_root(knowledge_root: Path, scenario_name: str) -> Path:
    """Resolve a scenario directory and ensure it stays under knowledge_root."""
    normalized = normalize_scenario_name_segment(scenario_name)
    root = knowledge_root.resolve(strict=False)
    candidate = (knowledge_root / normalized).resolve(strict=False)
    if candidate == root:
        raise ValueError(f"scenario_name must name a scenario subdirectory: {scenario_name!r}")
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"scenario_name escapes knowledge root: {scenario_name!r}") from exc
    return candidate


def scenario_skill_dir_name(scenario_name: str) -> str:
    """Return the skill directory name for a validated scenario name."""
    normalized = normalize_scenario_name_segment(scenario_name)
    return f"{normalized.replace('_', '-')}-ops"


def resolve_scenario_skill_dir(skills_root: Path, scenario_name: str) -> Path:
    """Resolve a scenario skill directory and ensure it stays under skills_root."""
    skill_dir_name = scenario_skill_dir_name(scenario_name)
    root = skills_root.resolve(strict=False)
    candidate = (skills_root / skill_dir_name).resolve(strict=False)
    if candidate == root:
        raise ValueError(f"scenario_name must name a scenario skill subdirectory: {scenario_name!r}")
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"scenario_name escapes skills root: {scenario_name!r}") from exc
    return candidate
