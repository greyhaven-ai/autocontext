"""File helpers for negative result ledger run artifacts."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Protocol

from autocontext.analytics.negative_result_ledger import NegativeResultApplicabilityContext, NegativeResultLedger
from autocontext.storage.scenario_paths import normalize_scenario_name_segment
from autocontext.util.json_io import read_json_guarded, write_json


class DictSerializable(Protocol):
    def to_dict(self) -> dict[str, Any]: ...


def negative_result_ledger_path(knowledge_root: Path, scenario_name: str, run_id: str) -> Path:
    filename = f"{_safe_run_id_segment(run_id)}.json"
    return knowledge_root / normalize_scenario_name_segment(scenario_name) / "negative_result_ledgers" / filename


def _safe_run_id_segment(run_id: str) -> str:
    """Map an arbitrary run identity to exactly one bounded path segment.

    The ledger body remains the source of truth for the original ``run_id``.
    Safe legacy names keep their existing filenames; unsafe, Unicode, or very
    long identities use a deterministic collision-resistant digest.
    """

    if not run_id:
        raise ValueError("run_id must be non-empty")
    if (
        run_id not in {".", ".."}
        and len(run_id.encode("utf-8")) <= 120
        and all(character.isascii() and (character.isalnum() or character in "-_.") for character in run_id)
    ):
        return run_id
    digest = hashlib.sha256(run_id.encode("utf-8")).hexdigest()
    return f"%{digest}"


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
    scenario_root = knowledge_root / normalize_scenario_name_segment(scenario_name)
    root = scenario_root / "negative_result_ledgers"
    paths = list(root.glob("*.json")) if root.exists() else []
    # PRs before the canonical index was added stored context-candidate ledgers
    # beside their evidence. Continue discovering those immutable artifacts.
    paths.extend((scenario_root / "context_bundles" / "candidates").glob("*/negative_result.json"))
    if not paths:
        return ""
    paths.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    parts: list[str] = []
    seen: set[tuple[str, tuple[str, ...]]] = set()
    for path in paths:
        ledger = read_negative_result_ledger_path(path)
        if ledger is None:
            continue
        bundle_digests = tuple(
            sorted(
                {
                    entry.context.context_bundle_digest
                    for entry in ledger.entries
                    if entry.context.context_bundle_digest is not None
                }
            )
        )
        identity = (ledger.run_id, bundle_digests)
        if identity in seen:
            continue
        seen.add(identity)
        parts.append(ledger.to_markdown(applicability_context=applicability_context))
        if len(parts) >= max_ledgers:
            break
    return "\n\n".join(parts)


__all__ = [
    "negative_result_ledger_path",
    "read_latest_negative_result_ledgers_markdown",
    "read_negative_result_ledger",
    "write_negative_result_ledger",
]
