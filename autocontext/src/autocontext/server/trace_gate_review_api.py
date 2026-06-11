from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from autocontext.analytics.cross_runtime_trace_findings import CrossRuntimeTraceFindingReport
from autocontext.analytics.trace_gate_operator_view import build_trace_gate_operator_view
from autocontext.storage.run_paths import resolve_run_root


def build_run_trace_gate_review(*, runs_root: Path, run_id: str) -> dict[str, Any]:
    """Read the operator-facing trace-finding/proposal/gate view for a run."""
    run_root = resolve_run_root(runs_root, run_id)
    return build_trace_gate_operator_view(
        run_id=run_id.strip(),
        report=_load_latest_trace_finding_report(run_root),
        proposals=_load_harness_change_proposals(run_root),
    )


def _load_latest_trace_finding_report(run_root: Path) -> CrossRuntimeTraceFindingReport | None:
    candidates = [
        *_json_files(run_root / "trace-findings"),
        *_json_files(run_root / "trace_findings"),
        run_root / "trace-finding-report.json",
        run_root / "trace_finding_report.json",
    ]
    existing = [path for path in candidates if path.exists()]
    if not existing:
        return None
    existing.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    payload = json.loads(existing[0].read_text(encoding="utf-8"))
    return CrossRuntimeTraceFindingReport.model_validate(payload)


def _load_harness_change_proposals(run_root: Path) -> list[dict[str, Any]]:
    proposals: list[dict[str, Any]] = []
    for path in [*_json_files(run_root / "harness-proposals"), *_json_files(run_root / "harness_proposals")]:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            proposals.append(payload)
    return proposals


def _json_files(directory: Path) -> list[Path]:
    if not directory.exists():
        return []
    return sorted(path for path in directory.glob("*.json") if path.is_file())
