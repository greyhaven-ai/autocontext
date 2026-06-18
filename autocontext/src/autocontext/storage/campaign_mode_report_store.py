"""File helpers for campaign-mode reports."""

from __future__ import annotations

from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Protocol

from autocontext.analytics.campaign_mode_report import CampaignModeReport
from autocontext.storage.scenario_paths import normalize_scenario_name_segment
from autocontext.util.json_io import read_json, write_json


class DictSerializable(Protocol):
    def to_dict(self) -> dict[str, Any]: ...


def campaign_mode_report_path(knowledge_root: Path, scenario_name: str, run_id: str) -> Path:
    return (
        knowledge_root
        / normalize_scenario_name_segment(scenario_name)
        / "campaign_mode_reports"
        / f"{_normalize_path_segment(run_id, 'run_id')}.json"
    )


def _normalize_path_segment(value: str, label: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{label} is required")
    if "/" in normalized or "\\" in normalized:
        raise ValueError(f"{label} must be a single path segment: {value!r}")
    for path_cls in (PurePosixPath, PureWindowsPath):
        candidate = path_cls(normalized)
        if candidate.is_absolute() or len(candidate.parts) != 1 or candidate.parts[0] in {".", ".."}:
            raise ValueError(f"{label} must be a single path segment: {value!r}")
    return normalized


def write_campaign_mode_report(knowledge_root: Path, scenario_name: str, run_id: str, report: DictSerializable) -> Path:
    path = campaign_mode_report_path(knowledge_root, scenario_name, run_id)
    write_json(path, report.to_dict())
    return path


def read_campaign_mode_report(knowledge_root: Path, scenario_name: str, run_id: str) -> CampaignModeReport | None:
    path = campaign_mode_report_path(knowledge_root, scenario_name, run_id)
    return CampaignModeReport.from_dict(read_json(path)) if path.exists() else None


def read_latest_campaign_mode_reports_markdown(
    knowledge_root: Path,
    scenario_name: str,
    *,
    max_reports: int = 2,
) -> str:
    root = knowledge_root / normalize_scenario_name_segment(scenario_name) / "campaign_mode_reports"
    if not root.exists():
        return ""
    paths = sorted(root.glob("*.json"), key=lambda path: path.stat().st_mtime, reverse=True)[:max_reports]
    return "\n\n".join(CampaignModeReport.from_dict(read_json(path)).to_markdown() for path in paths)


__all__ = [
    "campaign_mode_report_path",
    "read_campaign_mode_report",
    "read_latest_campaign_mode_reports_markdown",
    "write_campaign_mode_report",
]
