from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from autocontext.knowledge.context_selection import SCHEMA_VERSION, ContextSelectionDecision
from autocontext.storage.run_paths import resolve_run_root
from autocontext.util.json_io import read_json

if TYPE_CHECKING:
    from autocontext.storage.artifacts import ArtifactStore

_SAFE_STAGE_RE = re.compile(r"[A-Za-z0-9_.-]+")
_DECISION_FILE_RE = re.compile(r"gen_(?P<generation>[0-9]+)_(?P<stage>[A-Za-z0-9_.-]+)\.json\Z")


def context_selection_decision_path(
    runs_root: Path,
    decision: ContextSelectionDecision,
) -> Path:
    run_root = resolve_run_root(runs_root, decision.run_id)
    if decision.generation < 0:
        raise ValueError(f"generation must be non-negative: {decision.generation!r}")
    if not _SAFE_STAGE_RE.fullmatch(decision.stage):
        raise ValueError(f"stage must be a single safe path segment: {decision.stage!r}")
    return run_root / "context_selection" / f"gen_{decision.generation}_{decision.stage}.json"


def persist_context_selection_decision(
    artifacts: ArtifactStore,
    decision: ContextSelectionDecision,
) -> Path:
    path = context_selection_decision_path(artifacts.runs_root, decision)
    artifacts.write_json(path, decision.to_dict())
    return path


def load_context_selection_decisions(
    runs_root: Path,
    run_id: str,
) -> list[ContextSelectionDecision]:
    context_dir = resolve_run_root(runs_root, run_id) / "context_selection"
    if not context_dir.exists():
        return []
    decisions: list[ContextSelectionDecision] = []
    for path in sorted(context_dir.glob("gen_*_*.json")):
        match = _DECISION_FILE_RE.fullmatch(path.name)
        if match is None:
            continue
        data = read_json(path)
        decision = _decision_from_payload(
            data,
            run_id=run_id,
            generation=int(match.group("generation")),
            stage=match.group("stage"),
        )
        if decision is not None:
            decisions.append(decision)
    return sorted(decisions, key=lambda decision: (decision.generation, decision.stage))


def _decision_from_payload(
    data: Any,
    *,
    run_id: str,
    generation: int,
    stage: str,
) -> ContextSelectionDecision | None:
    if not isinstance(data, dict):
        return None
    if data.get("schema_version") != SCHEMA_VERSION:
        return None
    if data.get("run_id") != run_id:
        return None
    if type(data.get("generation")) is not int or data.get("generation") != generation:
        return None
    if data.get("stage") != stage or not _SAFE_STAGE_RE.fullmatch(stage):
        return None
    if not isinstance(data.get("scenario_name"), str):
        return None
    if not isinstance(data.get("candidates"), list):
        return None
    if not _has_decision_metrics(data.get("metrics")):
        return None
    return ContextSelectionDecision.from_dict(data)


def _has_decision_metrics(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    required_keys = {
        "candidate_count",
        "selected_count",
        "candidate_token_estimate",
        "selected_token_estimate",
    }
    return required_keys.issubset(value)
