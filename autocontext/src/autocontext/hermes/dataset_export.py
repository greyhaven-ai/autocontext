"""AC-705: export Hermes curator decision datasets for local training.

Read-only exporter that turns Hermes curator artifacts into supervised
training JSONL for narrow advisor classifiers (per the AC-708 scope).
Slice 1 ships the `curator-decisions` dataset kind; other documented
kinds (`consolidation-pairs`, `skill-selection`,
`skill-quality-signals`) raise `NotImplementedError` with a clear
message so callers know they are planned but not yet implemented.

## Label quality rules (AC-705)

- Curator `consolidated` and `pruned` are STRONG labels; the exporter
  emits them as `confidence="strong"`.
- `pinned` is a hard protection: pinned skills NEVER become mutation
  targets in the dataset, even when a curator run names them in an
  action list.
- Bundled / hub skills are out-of-scope as mutation targets; they
  appear only as context.
- If a skill appears in BOTH `consolidated` and `archived` (because
  consolidation can also archive the source), the stronger
  `consolidated` label wins so the dataset doesn't double-count.

## Output schema (curator-decisions)

Each JSONL row:

    {
      "example_id": "<short_run>:<skill>:<label>",
      "task_kind": "curator-decisions",
      "source": {
        "curator_run_path": "<absolute path to run.json>",
        "started_at": "<ISO 8601>"
      },
      "input": {
        "skill_name": "<name>",
        "skill_state": "active" | "archived" | "unknown",
        "skill_provenance": "agent-created" | "bundled" | "hub" | "unknown",
        "skill_pinned": <bool>,
        "skill_use_count": <int>,
        "skill_view_count": <int>,
        "skill_patch_count": <int>,
        "skill_activity_count": <int>,
        "skill_last_activity_at": "<ISO 8601 or null>"
      },
      "label": "consolidated" | "pruned" | "archived" | "added",
      "confidence": "strong",
      "redactions": [],
      "context": {
        "run_provider": "<provider>",
        "run_model": "<model>",
        "run_counts": { ... }
      }
    }

The schema is intentionally flat and feature-engineered so it can
feed `autoctx train --backend mlx|cuda` via a one-step adapter (the
adapter is a follow-up; this slice ships the dataset shape).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from autocontext.hermes.inspection import (
    CuratorRunSummary,
    HermesSkill,
    inspect_hermes_home,
)

_SUPPORTED_KINDS = frozenset({"curator-decisions"})
_PLANNED_KINDS = frozenset({"consolidation-pairs", "skill-selection", "skill-quality-signals"})


@dataclass(slots=True)
class ExportSummary:
    """What happened during one dataset export invocation."""

    runs_read: int = 0
    examples_written: int = 0
    output_path: Path | None = None
    warnings: list[str] = field(default_factory=list)


def export_dataset(
    *,
    kind: str,
    home: Path,
    output: Path,
    since: str | None = None,
    limit: int | None = None,
) -> ExportSummary:
    """Dispatch by dataset kind.

    `curator-decisions` is shipped; other documented kinds raise
    `NotImplementedError` with a clear message.
    """

    if kind == "curator-decisions":
        return export_curator_decisions(home=home, output=output, since=since, limit=limit)
    if kind in _PLANNED_KINDS:
        raise NotImplementedError(
            f"dataset kind {kind!r} is documented but not yet implemented; see AC-705 for the planned shape"
        )
    raise ValueError(f"unknown dataset kind {kind!r}; supported: {sorted(_SUPPORTED_KINDS)}, planned: {sorted(_PLANNED_KINDS)}")


def export_curator_decisions(
    *,
    home: Path,
    output: Path,
    since: str | None = None,
    limit: int | None = None,
) -> ExportSummary:
    """Emit a `curator-decisions` training dataset to ``output`` as JSONL.

    See module docstring for the row schema. Returns an
    :class:`ExportSummary` with counts and any warnings.
    """

    summary = ExportSummary(output_path=output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("", encoding="utf-8")

    if not home.exists():
        return summary

    inventory = inspect_hermes_home(home)
    skills_by_name = {skill.name: skill for skill in inventory.skills}
    summary.runs_read = inventory.curator.run_count

    since_dt = _parse_iso(since) if since is not None else None
    examples: list[dict[str, Any]] = []
    for run in inventory.curator.runs:
        if limit is not None and len(examples) >= limit:
            break
        started_at_dt = _parse_iso(run.started_at) if run.started_at else None
        if since_dt is not None and started_at_dt is not None and started_at_dt < since_dt:
            continue

        # Strongest-label-wins precedence: consolidated > pruned > archived > added.
        # A skill that appears in multiple action lists gets a single example
        # with the strongest label, so the dataset never double-counts.
        actions = _collect_action_labels(run)
        for skill_name, label in actions.items():
            if limit is not None and len(examples) >= limit:
                break
            skill = skills_by_name.get(skill_name)
            if not _is_valid_target(skill):
                continue
            example = _build_example(
                run=run,
                skill_name=skill_name,
                skill=skill,
                label=label,
            )
            examples.append(example)

    if examples:
        with output.open("w", encoding="utf-8") as fh:
            for example in examples:
                fh.write(json.dumps(example, separators=(",", ":")) + "\n")
    summary.examples_written = len(examples)
    return summary


def _collect_action_labels(run: CuratorRunSummary) -> dict[str, str]:
    """Return {skill_name: label} with the strongest label per skill.

    Precedence: consolidated > pruned > archived > added. A skill that
    appears in multiple lists gets a single labeled example.
    """
    data = _read_run_json(run.path)
    consolidated = _as_str_list(data.get("consolidated"))
    pruned = _as_str_list(data.get("pruned"))
    archived = _as_str_list(data.get("archived"))
    added = _as_str_list(data.get("added"))

    labels: dict[str, str] = {}
    for name in consolidated:
        labels[name] = "consolidated"
    for name in pruned:
        labels.setdefault(name, "pruned")
    for name in archived:
        labels.setdefault(name, "archived")
    for name in added:
        labels.setdefault(name, "added")
    return labels


def _is_valid_target(skill: HermesSkill | None) -> bool:
    """A `pinned` skill is a hard protection: never a mutation target.
    `bundled` / `hub` skills are out-of-scope as targets; they're
    context only. A skill missing from the inventory still counts as a
    valid target (historical decision; the example is emitted with
    `unknown` features)."""
    if skill is None:
        return True
    if skill.pinned:
        return False
    if skill.provenance in {"bundled", "hub"}:
        return False
    return True


def _build_example(
    *,
    run: CuratorRunSummary,
    skill_name: str,
    skill: HermesSkill | None,
    label: str,
) -> dict[str, Any]:
    if skill is not None:
        input_features = {
            "skill_name": skill.name,
            "skill_state": skill.state,
            "skill_provenance": skill.provenance,
            "skill_pinned": skill.pinned,
            "skill_use_count": skill.use_count,
            "skill_view_count": skill.view_count,
            "skill_patch_count": skill.patch_count,
            "skill_activity_count": skill.activity_count,
            "skill_last_activity_at": skill.last_activity_at,
        }
    else:
        input_features = {
            "skill_name": skill_name,
            "skill_state": "unknown",
            "skill_provenance": "unknown",
            "skill_pinned": False,
            "skill_use_count": 0,
            "skill_view_count": 0,
            "skill_patch_count": 0,
            "skill_activity_count": 0,
            "skill_last_activity_at": None,
        }

    short_run = run.path.parent.name
    return {
        "example_id": f"{short_run}:{skill_name}:{label}",
        "task_kind": "curator-decisions",
        "source": {
            "curator_run_path": str(run.path),
            "started_at": run.started_at,
        },
        "input": input_features,
        "label": label,
        "confidence": "strong",
        "redactions": [],
        "context": {
            "run_provider": run.provider,
            "run_model": run.model,
            "run_counts": dict(run.counts),
        },
    }


def _read_run_json(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_text(encoding="utf-8")
        parsed = json.loads(raw)
    except (OSError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _parse_iso(value: str) -> datetime | None:
    if not value:
        return None
    text = value.strip().replace("Z", "+00:00") if value.endswith("Z") else value
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _as_str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


__all__ = [
    "ExportSummary",
    "export_curator_decisions",
    "export_dataset",
]
