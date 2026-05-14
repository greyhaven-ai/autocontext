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
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from autocontext.hermes.inspection import (
    CuratorRunSummary,
    HermesInventory,
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
    # PR #964 review (P2): a name listed in `.usage.json` as pinned, or
    # in `.bundled_manifest` / `.hub/lock.json`, is protected even when
    # it's no longer in the active SKILL.md inventory. Build that set
    # once and check membership before emitting a strong label.
    protected_names = _collect_protected_names(home=home, inventory=inventory)
    summary.runs_read = inventory.curator.run_count

    # PR #964 review (P2): reject invalid `--since` at the boundary
    # instead of silently disabling the filter; ensure aware UTC so
    # comparisons against run timestamps cannot raise TypeError.
    since_dt: datetime | None = None
    if since is not None:
        since_dt = _parse_iso(since)
        if since_dt is None:
            raise ValueError(f"invalid --since value {since!r}; expected ISO-8601 timestamp")
        if since_dt.tzinfo is None:
            since_dt = since_dt.replace(tzinfo=UTC)

    examples: list[dict[str, Any]] = []
    for run in inventory.curator.runs:
        if limit is not None and len(examples) >= limit:
            break

        # Compute effective started_at: parsed run.started_at if present
        # and parseable, file mtime otherwise. Ensures missing
        # started_at still honors --since (PR #964 review P2).
        effective_dt = _effective_started_at(run)
        if since_dt is not None and effective_dt < since_dt:
            continue

        # Strongest-label-wins precedence: consolidated > pruned > archived > added.
        # A skill that appears in multiple action lists gets a single example
        # with the strongest label, so the dataset never double-counts.
        actions = _collect_action_labels(run)
        for skill_name, label in actions.items():
            if limit is not None and len(examples) >= limit:
                break
            skill = skills_by_name.get(skill_name)
            if not _is_valid_target(skill_name=skill_name, skill=skill, protected_names=protected_names):
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
    consolidated = _as_name_list(data.get("consolidated"))
    pruned = _as_name_list(data.get("pruned"))
    archived = _as_name_list(data.get("archived"))
    added = _as_name_list(data.get("added"))

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


def _is_valid_target(
    *,
    skill_name: str,
    skill: HermesSkill | None,
    protected_names: set[str],
) -> bool:
    """Strong-label gate.

    Protections (any of these block a skill from being a mutation
    target):
    - `skill.pinned` is True;
    - `skill.provenance` is `bundled` or `hub`;
    - the name appears in the protected-name set derived from raw
      `.usage.json` / `.bundled_manifest` / `.hub/lock.json` even when
      the skill is no longer in the active inventory (PR #964 review P2).

    Skills missing from BOTH the active inventory AND the protected
    set still emit a strong-label row (historical decision use case)
    with `skill_*` features set to `unknown`/0/False.
    """
    if skill_name in protected_names:
        return False
    if skill is None:
        return True
    if skill.pinned:
        return False
    if skill.provenance in {"bundled", "hub"}:
        return False
    return True


def _collect_protected_names(*, home: Path, inventory: HermesInventory) -> set[str]:
    """Names that should never be mutation targets, even when the active
    SKILL.md tree no longer contains them.

    Sources:
    - active-inventory skills with `pinned` / `provenance in (bundled, hub)`
    - `.usage.json` entries marked `pinned: true`
    - `.bundled_manifest` lines (one name per line, optional `:` suffix)
    - `.hub/lock.json` `installed` keys

    The set is queried once per ingest call.
    """
    protected: set[str] = set()
    for skill in inventory.skills:
        if skill.pinned or skill.provenance in {"bundled", "hub"}:
            protected.add(skill.name)

    skills_dir = home / "skills"
    usage_path = skills_dir / ".usage.json"
    if usage_path.exists():
        try:
            raw = json.loads(usage_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raw = None
        if isinstance(raw, dict):
            for name, record in raw.items():
                if isinstance(record, dict) and bool(record.get("pinned")):
                    protected.add(str(name))

    bundled_path = skills_dir / ".bundled_manifest"
    if bundled_path.exists():
        try:
            for line in bundled_path.read_text(encoding="utf-8").splitlines():
                name = line.strip().split(":", 1)[0].strip()
                if name:
                    protected.add(name)
        except OSError:
            pass

    hub_path = skills_dir / ".hub" / "lock.json"
    if hub_path.exists():
        try:
            raw = json.loads(hub_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raw = None
        if isinstance(raw, dict):
            installed = raw.get("installed")
            if isinstance(installed, dict):
                protected.update(str(name) for name in installed.keys())

    return protected


def _effective_started_at(run: CuratorRunSummary) -> datetime:
    """Aware UTC datetime: parsed `run.started_at` if present, else file
    mtime. Used by --since filtering so missing-start runs cannot
    bypass incremental imports (PR #964 review P2)."""
    if run.started_at:
        parsed = _parse_iso(run.started_at)
        if parsed is not None:
            return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
    return datetime.fromtimestamp(run.path.stat().st_mtime, tz=UTC)


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


def _as_name_list(value: Any) -> list[str]:
    """Accept both Hermes v0.12 action shapes: a list of strings OR a
    list of `{"name": ...}` dicts. Drops entries with no usable name
    (PR #964 review P1).
    """
    if not isinstance(value, list):
        return []
    names: list[str] = []
    for item in value:
        if isinstance(item, str):
            names.append(item)
        elif isinstance(item, dict):
            name = item.get("name")
            if isinstance(name, str):
                names.append(name)
    return names


__all__ = [
    "ExportSummary",
    "export_curator_decisions",
    "export_dataset",
]
