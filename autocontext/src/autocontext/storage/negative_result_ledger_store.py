"""File helpers for negative result ledger run artifacts."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

from autocontext.analytics.negative_result_ledger import NegativeResultLedger
from autocontext.storage.scenario_paths import normalize_scenario_name_segment
from autocontext.util.json_io import read_json, write_json


class DictSerializable(Protocol):
    def to_dict(self) -> dict[str, Any]: ...


def negative_result_ledger_path(knowledge_root: Path, scenario_name: str, run_id: str) -> Path:
    return knowledge_root / normalize_scenario_name_segment(scenario_name) / "negative_result_ledgers" / f"{run_id}.json"


def write_negative_result_ledger(knowledge_root: Path, scenario_name: str, run_id: str, ledger: DictSerializable) -> Path:
    path = negative_result_ledger_path(knowledge_root, scenario_name, run_id)
    write_json(path, ledger.to_dict())
    return path


def read_negative_result_ledger(knowledge_root: Path, scenario_name: str, run_id: str) -> NegativeResultLedger | None:
    path = negative_result_ledger_path(knowledge_root, scenario_name, run_id)
    return NegativeResultLedger.from_dict(read_json(path)) if path.exists() else None


def read_latest_negative_result_ledgers_markdown(
    knowledge_root: Path,
    scenario_name: str,
    *,
    max_ledgers: int = 2,
) -> str:
    root = knowledge_root / normalize_scenario_name_segment(scenario_name) / "negative_result_ledgers"
    if not root.exists():
        return ""
    paths = sorted(root.glob("*.json"), key=lambda path: path.stat().st_mtime, reverse=True)[:max_ledgers]
    return "\n\n".join(NegativeResultLedger.from_dict(read_json(path)).to_markdown() for path in paths)


__all__ = [
    "negative_result_ledger_path",
    "read_latest_negative_result_ledgers_markdown",
    "read_negative_result_ledger",
    "write_negative_result_ledger",
]
