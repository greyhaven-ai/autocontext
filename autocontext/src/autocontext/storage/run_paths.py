"""Helpers for resolving per-run filesystem paths."""

from __future__ import annotations

from pathlib import Path


def resolve_run_root(runs_root: Path, run_id: str) -> Path:
    """Resolve a run directory and ensure it stays under runs_root."""
    normalized = run_id.strip()
    if not normalized:
        raise ValueError("run_id is required")

    root = runs_root.resolve()
    candidate = (runs_root / normalized).resolve()
    if candidate == root:
        raise ValueError(f"run_id must name a run subdirectory: {run_id!r}")
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"run_id escapes runs root: {run_id!r}") from exc
    return candidate
