"""File helpers for negative result ledger run artifacts."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

from autocontext.analytics.negative_result_ledger import NegativeResultApplicabilityContext, NegativeResultLedger
from autocontext.storage.scenario_paths import normalize_scenario_name_segment
from autocontext.util.json_io import read_json_guarded, write_json


class DictSerializable(Protocol):
    def to_dict(self) -> dict[str, Any]: ...


def negative_result_ledger_path(knowledge_root: Path, scenario_name: str, run_id: str) -> Path:
    return knowledge_root / normalize_scenario_name_segment(scenario_name) / "negative_result_ledgers" / f"{run_id}.json"


def write_negative_result_ledger(knowledge_root: Path, scenario_name: str, run_id: str, ledger: DictSerializable) -> Path:
    path = negative_result_ledger_path(knowledge_root, scenario_name, run_id)
    write_json(path, ledger.to_dict())
    return path


def read_negative_result_ledger_path(path: Path) -> NegativeResultLedger | None:
    """Parse one ledger file; corrupt files degrade to None."""
    data = read_json_guarded(path)
    if not isinstance(data, dict):
        return None
    try:
        return NegativeResultLedger.from_dict(data)
    except (TypeError, ValueError):
        return None


def read_negative_result_ledger(knowledge_root: Path, scenario_name: str, run_id: str) -> NegativeResultLedger | None:
    path = negative_result_ledger_path(knowledge_root, scenario_name, run_id)
    if not path.exists():
        return None
    data = read_json_guarded(path)
    if not isinstance(data, dict):
        return None
    try:
        return NegativeResultLedger.from_dict(data)
    except (TypeError, ValueError):
        return None


def read_latest_negative_result_ledgers_markdown(
    knowledge_root: Path,
    scenario_name: str,
    *,
    max_ledgers: int = 2,
    applicability_context: NegativeResultApplicabilityContext | None = None,
) -> str:
    root = knowledge_root / normalize_scenario_name_segment(scenario_name) / "negative_result_ledgers"
    if not root.exists():
        return ""
    paths = sorted(root.glob("*.json"), key=lambda path: path.stat().st_mtime, reverse=True)[:max_ledgers]
    parts: list[str] = []
    for path in paths:
        ledger = read_negative_result_ledger_path(path)
        if ledger is not None:
            parts.append(ledger.to_markdown(applicability_context=applicability_context))
    return "\n\n".join(parts)


__all__ = [
    "negative_result_ledger_path",
    "read_latest_negative_result_ledgers_markdown",
    "read_negative_result_ledger",
    "write_negative_result_ledger",
]
