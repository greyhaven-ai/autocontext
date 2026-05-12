from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

from autocontext.knowledge.context_selection import ContextSelectionDecision
from autocontext.storage.run_paths import resolve_run_root

if TYPE_CHECKING:
    from autocontext.storage.artifacts import ArtifactStore

_SAFE_STAGE_RE = re.compile(r"[A-Za-z0-9_.-]+")


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
