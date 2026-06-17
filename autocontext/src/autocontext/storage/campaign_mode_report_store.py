"""File helpers for campaign-mode reports."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

from autocontext.analytics.campaign_mode_report import CampaignModeReport
from autocontext.storage.scenario_paths import normalize_scenario_name_segment
from autocontext.util.json_io import read_json, write_json


class DictSerializable(Protocol):
    def to_dict(self) -> dict[str, Any]: ...


def campaign_mode_report_path(knowledge_root: Path, scenario_name: str, run_id: str) -> Path:
    return knowledge_root / normalize_scenario_name_segment(scenario_name) / "campaign_mode_reports" / f"{run_id}.json"


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
