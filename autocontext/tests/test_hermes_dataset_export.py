"""AC-705: export Hermes curator decision datasets for local training.

Tests use a helper to plant a minimal Hermes home (skills + usage +
curator run.json), then assert the curator-decisions exporter produces
training JSONL rows that:

* carry strong labels (consolidated / pruned / archived / added) from
  curator action lists,
* never list a `pinned` skill as a mutation target,
* never list a `bundled` or `hub` skill as a mutation target,
* preserve enough source/context metadata for reproducible evaluation,
* document a stable example_id derived from run_path + skill + label.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from autocontext.hermes.dataset_export import (
    ExportSummary,
    export_curator_decisions,
)


def _plant_hermes_home(
    tmp_path: Path,
    *,
    skills: list[dict],
    usage: dict[str, dict] | None = None,
    curator_runs: list[dict],
) -> Path:
    """Build a minimal Hermes home for tests."""

    home = tmp_path / "hermes"
    skills_dir = home / "skills"
    skills_dir.mkdir(parents=True)

    # Auto-populate usage so `pinned`/`state` declarations on skills survive
    # into the parsed inventory (the real Hermes layout keeps these in
    # `.usage.json`, not in SKILL.md frontmatter).
    auto_usage: dict[str, dict] = {}
    for skill in skills:
        name = skill["name"]
        skill_dir = skills_dir / name
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: {skill.get('description', 'test skill')}\n---\n# {name}\n",
            encoding="utf-8",
        )
        record: dict[str, object] = {
            "state": skill.get("state", "active"),
            "pinned": bool(skill.get("pinned", False)),
        }
        for field_name in ("use_count", "view_count", "patch_count"):
            if field_name in skill:
                record[field_name] = skill[field_name]
        auto_usage[name] = record

    merged_usage = {**auto_usage, **(usage or {})}
    (skills_dir / ".usage.json").write_text(json.dumps(merged_usage), encoding="utf-8")

    bundled_names = [s["name"] for s in skills if s.get("provenance") == "bundled"]
    hub_names = [s["name"] for s in skills if s.get("provenance") == "hub"]
    if bundled_names:
        (skills_dir / ".bundled_manifest").write_text("\n".join(bundled_names) + "\n", encoding="utf-8")
    if hub_names:
        hub_dir = skills_dir / ".hub"
        hub_dir.mkdir()
        (hub_dir / "lock.json").write_text(json.dumps({"installed": {n: {} for n in hub_names}}), encoding="utf-8")

    curator_root = home / "logs" / "curator"
    curator_root.mkdir(parents=True)
    for run in curator_runs:
        run_dir = curator_root / run["run_id"]
        run_dir.mkdir()
        (run_dir / "run.json").write_text(json.dumps(run["data"]), encoding="utf-8")

    return home


def _load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_consolidated_skill_becomes_strong_label(tmp_path: Path) -> None:
    home = _plant_hermes_home(
        tmp_path,
        skills=[
            {"name": "skill-a", "provenance": "agent-created"},
        ],
        usage={"skill-a": {"use_count": 12, "view_count": 3, "patch_count": 1}},
        curator_runs=[
            {
                "run_id": "run-001",
                "data": {
                    "started_at": "2026-05-13T15:00:00Z",
                    "duration_seconds": 10.0,
                    "provider": "anthropic",
                    "model": "claude-sonnet-4-5",
                    "consolidated": ["skill-a"],
                    "pruned": [],
                    "archived": [],
                    "added": [],
                },
            },
        ],
    )
    output = tmp_path / "out.jsonl"

    summary = export_curator_decisions(home=home, output=output)

    assert isinstance(summary, ExportSummary)
    assert summary.examples_written == 1
    rows = _load_jsonl(output)
    assert rows[0]["label"] == "consolidated"
    assert rows[0]["confidence"] == "strong"
    assert rows[0]["input"]["skill_name"] == "skill-a"
    assert rows[0]["input"]["skill_use_count"] == 12


def test_pinned_skill_is_never_a_mutation_target(tmp_path: Path) -> None:
    """Pinned skills are hard-protected. Even if a curator run somehow
    lists them as consolidated/pruned/archived, the exporter must NOT
    emit a training example with the pinned skill as the label target."""
    home = _plant_hermes_home(
        tmp_path,
        skills=[
            {"name": "pinned-skill", "provenance": "agent-created", "pinned": True},
            {"name": "normal-skill", "provenance": "agent-created"},
        ],
        curator_runs=[
            {
                "run_id": "run-001",
                "data": {
                    "started_at": "2026-05-13T15:00:00Z",
                    "duration_seconds": 5.0,
                    "provider": "anthropic",
                    "model": "claude-sonnet-4-5",
                    "consolidated": ["pinned-skill", "normal-skill"],
                    "pruned": [],
                    "archived": [],
                    "added": [],
                },
            },
        ],
    )
    output = tmp_path / "out.jsonl"

    export_curator_decisions(home=home, output=output)
    rows = _load_jsonl(output)
    target_names = {r["input"]["skill_name"] for r in rows}
    assert "pinned-skill" not in target_names
    assert "normal-skill" in target_names


def test_bundled_skill_is_never_a_mutation_target(tmp_path: Path) -> None:
    home = _plant_hermes_home(
        tmp_path,
        skills=[
            {"name": "bundled-skill", "provenance": "bundled"},
            {"name": "agent-skill", "provenance": "agent-created"},
        ],
        curator_runs=[
            {
                "run_id": "run-001",
                "data": {
                    "started_at": "2026-05-13T15:00:00Z",
                    "duration_seconds": 5.0,
                    "provider": "anthropic",
                    "model": "claude-sonnet-4-5",
                    "consolidated": [],
                    "pruned": ["bundled-skill", "agent-skill"],
                    "archived": [],
                    "added": [],
                },
            },
        ],
    )
    output = tmp_path / "out.jsonl"

    export_curator_decisions(home=home, output=output)
    rows = _load_jsonl(output)
    target_names = {r["input"]["skill_name"] for r in rows}
    assert "bundled-skill" not in target_names
    assert "agent-skill" in target_names


def test_hub_skill_is_never_a_mutation_target(tmp_path: Path) -> None:
    home = _plant_hermes_home(
        tmp_path,
        skills=[
            {"name": "hub-skill", "provenance": "hub"},
            {"name": "agent-skill", "provenance": "agent-created"},
        ],
        curator_runs=[
            {
                "run_id": "run-001",
                "data": {
                    "started_at": "2026-05-13T15:00:00Z",
                    "duration_seconds": 5.0,
                    "provider": "anthropic",
                    "model": "claude-sonnet-4-5",
                    "consolidated": [],
                    "pruned": [],
                    "archived": ["hub-skill", "agent-skill"],
                    "added": [],
                },
            },
        ],
    )
    output = tmp_path / "out.jsonl"
    export_curator_decisions(home=home, output=output)
    rows = _load_jsonl(output)
    target_names = {r["input"]["skill_name"] for r in rows}
    assert "hub-skill" not in target_names
    assert "agent-skill" in target_names


def test_added_skill_carries_added_label(tmp_path: Path) -> None:
    home = _plant_hermes_home(
        tmp_path,
        skills=[
            {"name": "new-skill", "provenance": "agent-created"},
        ],
        curator_runs=[
            {
                "run_id": "run-001",
                "data": {
                    "started_at": "2026-05-13T15:00:00Z",
                    "duration_seconds": 5.0,
                    "provider": "anthropic",
                    "model": "claude-sonnet-4-5",
                    "consolidated": [],
                    "pruned": [],
                    "archived": [],
                    "added": ["new-skill"],
                },
            },
        ],
    )
    output = tmp_path / "out.jsonl"
    export_curator_decisions(home=home, output=output)
    rows = _load_jsonl(output)
    labels = {r["label"] for r in rows}
    assert labels == {"added"}


def test_example_row_carries_source_metadata_and_context(tmp_path: Path) -> None:
    home = _plant_hermes_home(
        tmp_path,
        skills=[{"name": "skill-x", "provenance": "agent-created"}],
        curator_runs=[
            {
                "run_id": "run-abc",
                "data": {
                    "started_at": "2026-05-13T15:00:00Z",
                    "duration_seconds": 10.0,
                    "provider": "anthropic",
                    "model": "claude-sonnet-4-5",
                    "counts": {"consolidated_this_run": 1},
                    "consolidated": ["skill-x"],
                },
            },
        ],
    )
    output = tmp_path / "out.jsonl"
    export_curator_decisions(home=home, output=output)
    row = _load_jsonl(output)[0]

    # Source metadata reproducible
    assert "curator_run_path" in row["source"]
    assert row["source"]["started_at"] == "2026-05-13T15:00:00Z"
    assert "skill-x" in row["example_id"]
    assert "consolidated" in row["example_id"]

    # Context features
    assert row["context"]["run_provider"] == "anthropic"
    assert row["context"]["run_model"] == "claude-sonnet-4-5"
    assert row["context"]["run_counts"]["consolidated_this_run"] == 1

    # Task kind explicit
    assert row["task_kind"] == "curator-decisions"


def test_archived_distinguishes_consolidated_versus_pruned(tmp_path: Path) -> None:
    """If a skill appears in BOTH the `consolidated` list and the `archived`
    list (because consolidation can also archive the source), the strong
    label is `consolidated`, not `archived`. The exporter should emit
    only the stronger label to avoid double-counting."""
    home = _plant_hermes_home(
        tmp_path,
        skills=[{"name": "skill-c", "provenance": "agent-created"}],
        curator_runs=[
            {
                "run_id": "run-001",
                "data": {
                    "started_at": "2026-05-13T15:00:00Z",
                    "duration_seconds": 5.0,
                    "provider": "anthropic",
                    "model": "claude-sonnet-4-5",
                    "consolidated": ["skill-c"],
                    "archived": ["skill-c"],
                },
            },
        ],
    )
    output = tmp_path / "out.jsonl"
    export_curator_decisions(home=home, output=output)
    rows = _load_jsonl(output)
    assert len(rows) == 1
    assert rows[0]["label"] == "consolidated"


def test_skill_not_in_inventory_still_emits_example_with_unknown_features(tmp_path: Path) -> None:
    """If a curator run names a skill that's no longer in the skills tree
    (already archived or pruned earlier), we still emit a training example
    but mark the unknown features explicitly. Useful for training advisor
    models on historical decisions."""
    home = _plant_hermes_home(
        tmp_path,
        skills=[],  # empty skills dir
        curator_runs=[
            {
                "run_id": "run-001",
                "data": {
                    "started_at": "2026-05-13T15:00:00Z",
                    "duration_seconds": 5.0,
                    "provider": "anthropic",
                    "model": "claude-sonnet-4-5",
                    "consolidated": ["gone-skill"],
                },
            },
        ],
    )
    output = tmp_path / "out.jsonl"
    export_curator_decisions(home=home, output=output)
    rows = _load_jsonl(output)
    assert len(rows) == 1
    assert rows[0]["input"]["skill_name"] == "gone-skill"
    assert rows[0]["input"]["skill_state"] == "unknown"
    assert rows[0]["input"]["skill_provenance"] == "unknown"
    assert rows[0]["input"]["skill_pinned"] is False


def test_since_filter_drops_older_runs(tmp_path: Path) -> None:
    home = _plant_hermes_home(
        tmp_path,
        skills=[
            {"name": "skill-old", "provenance": "agent-created"},
            {"name": "skill-new", "provenance": "agent-created"},
        ],
        curator_runs=[
            {
                "run_id": "old",
                "data": {
                    "started_at": "2026-05-01T00:00:00Z",
                    "duration_seconds": 1.0,
                    "consolidated": ["skill-old"],
                },
            },
            {
                "run_id": "new",
                "data": {
                    "started_at": "2026-05-13T15:00:00Z",
                    "duration_seconds": 1.0,
                    "consolidated": ["skill-new"],
                },
            },
        ],
    )
    output = tmp_path / "out.jsonl"
    export_curator_decisions(home=home, output=output, since="2026-05-10T00:00:00Z")
    rows = _load_jsonl(output)
    target_names = {r["input"]["skill_name"] for r in rows}
    assert target_names == {"skill-new"}


def test_limit_caps_examples_emitted(tmp_path: Path) -> None:
    home = _plant_hermes_home(
        tmp_path,
        skills=[{"name": f"skill-{i}", "provenance": "agent-created"} for i in range(5)],
        curator_runs=[
            {
                "run_id": "run-001",
                "data": {
                    "started_at": "2026-05-13T15:00:00Z",
                    "duration_seconds": 1.0,
                    "consolidated": [f"skill-{i}" for i in range(5)],
                },
            },
        ],
    )
    output = tmp_path / "out.jsonl"
    summary = export_curator_decisions(home=home, output=output, limit=3)
    rows = _load_jsonl(output)
    assert len(rows) == 3
    assert summary.examples_written == 3


def test_empty_home_produces_empty_output(tmp_path: Path) -> None:
    home = tmp_path / "empty-home"
    home.mkdir()
    output = tmp_path / "out.jsonl"
    summary = export_curator_decisions(home=home, output=output)
    assert summary.examples_written == 0
    assert output.exists()
    assert output.read_text(encoding="utf-8") == ""


def test_unknown_kind_raises(tmp_path: Path) -> None:
    """The exporter ships with `curator-decisions`; other kinds
    (`consolidation-pairs`, `skill-selection`, `skill-quality-signals`)
    are documented but not yet implemented. They must fail loudly with a
    clear NotImplementedError rather than silently emit nothing."""
    from autocontext.hermes.dataset_export import export_dataset

    home = tmp_path / "empty-home"
    home.mkdir()
    output = tmp_path / "out.jsonl"
    with pytest.raises(NotImplementedError, match="consolidation-pairs"):
        export_dataset(kind="consolidation-pairs", home=home, output=output)


def test_object_shape_actions_emit_examples(tmp_path: Path) -> None:
    """PR #964 review (P1): real Hermes v0.12 Curator action objects use
    `[{"name": "...", ...}, ...]` not `["...", ...]`. Both shapes must
    produce training rows so the exporter doesn't silently emit zero
    examples against a real Curator run."""
    home = _plant_hermes_home(
        tmp_path,
        skills=[{"name": "skill-obj", "provenance": "agent-created"}],
        curator_runs=[
            {
                "run_id": "run-001",
                "data": {
                    "started_at": "2026-05-13T15:00:00Z",
                    "duration_seconds": 5.0,
                    "provider": "anthropic",
                    "model": "claude-sonnet-4-5",
                    "consolidated": [
                        {"name": "skill-obj", "reason": "merged with sibling"},
                    ],
                    "pruned": [],
                    "archived": [],
                    "added": [],
                },
            },
        ],
    )
    output = tmp_path / "out.jsonl"
    summary = export_curator_decisions(home=home, output=output)
    rows = _load_jsonl(output)
    assert summary.examples_written == 1
    assert rows[0]["input"]["skill_name"] == "skill-obj"
    assert rows[0]["label"] == "consolidated"


def test_pinned_via_usage_json_blocks_target_when_skill_missing(tmp_path: Path) -> None:
    """PR #964 review (P2): a name marked `pinned: true` in `.usage.json`
    must remain protected even when the SKILL.md folder has been removed
    (skill not in the active inventory). Otherwise the exporter would
    treat the missing-but-pinned skill as a normal mutation target with
    skill_pinned=False."""
    home = _plant_hermes_home(
        tmp_path,
        skills=[],  # no active SKILL.md folders
        usage={"pinned-ghost": {"state": "active", "pinned": True}},
        curator_runs=[
            {
                "run_id": "run-001",
                "data": {
                    "started_at": "2026-05-13T15:00:00Z",
                    "duration_seconds": 5.0,
                    "provider": "anthropic",
                    "model": "claude-sonnet-4-5",
                    "consolidated": ["pinned-ghost"],
                },
            },
        ],
    )
    output = tmp_path / "out.jsonl"
    export_curator_decisions(home=home, output=output)
    rows = _load_jsonl(output)
    assert rows == []


def test_bundled_manifest_blocks_target_when_skill_missing(tmp_path: Path) -> None:
    """PR #964 review (P2): a name in `.bundled_manifest` is upstream-
    owned and must not become a mutation target even when no active
    SKILL.md folder exists for it."""
    home = tmp_path / "hermes"
    skills_dir = home / "skills"
    skills_dir.mkdir(parents=True)
    (skills_dir / ".bundled_manifest").write_text("bundled-ghost\n", encoding="utf-8")
    curator_root = home / "logs" / "curator"
    curator_root.mkdir(parents=True)
    (curator_root / "run-001").mkdir()
    (curator_root / "run-001" / "run.json").write_text(
        json.dumps(
            {
                "started_at": "2026-05-13T15:00:00Z",
                "duration_seconds": 5.0,
                "provider": "anthropic",
                "model": "claude-sonnet-4-5",
                "pruned": ["bundled-ghost"],
            }
        ),
        encoding="utf-8",
    )
    output = tmp_path / "out.jsonl"
    export_curator_decisions(home=home, output=output)
    rows = _load_jsonl(output)
    assert rows == []


def test_hub_lock_blocks_target_when_skill_missing(tmp_path: Path) -> None:
    """PR #964 review (P2): a name in `.hub/lock.json` is hub-installed
    and must not become a mutation target even when no active SKILL.md
    folder exists for it."""
    home = tmp_path / "hermes"
    skills_dir = home / "skills"
    (skills_dir / ".hub").mkdir(parents=True)
    (skills_dir / ".hub" / "lock.json").write_text(
        json.dumps({"installed": {"hub-ghost": {"version": "1.0"}}}),
        encoding="utf-8",
    )
    curator_root = home / "logs" / "curator"
    curator_root.mkdir(parents=True)
    (curator_root / "run-001").mkdir()
    (curator_root / "run-001" / "run.json").write_text(
        json.dumps(
            {
                "started_at": "2026-05-13T15:00:00Z",
                "duration_seconds": 5.0,
                "provider": "anthropic",
                "model": "claude-sonnet-4-5",
                "archived": ["hub-ghost"],
            }
        ),
        encoding="utf-8",
    )
    output = tmp_path / "out.jsonl"
    export_curator_decisions(home=home, output=output)
    rows = _load_jsonl(output)
    assert rows == []


def test_invalid_since_raises_value_error(tmp_path: Path) -> None:
    """PR #964 review (P2): silently disabling --since on a parse failure
    hides operator mistakes. Invalid ISO timestamps must surface as a
    ValueError so the caller can correct the input."""
    home = _plant_hermes_home(
        tmp_path,
        skills=[{"name": "skill-a", "provenance": "agent-created"}],
        curator_runs=[
            {
                "run_id": "run-001",
                "data": {
                    "started_at": "2026-05-13T15:00:00Z",
                    "duration_seconds": 5.0,
                    "consolidated": ["skill-a"],
                },
            },
        ],
    )
    output = tmp_path / "out.jsonl"
    with pytest.raises(ValueError, match="invalid --since"):
        export_curator_decisions(home=home, output=output, since="not-a-date")


def test_since_filter_applies_to_mtime_fallback_when_started_at_missing(tmp_path: Path) -> None:
    """PR #964 review (P2): runs without a parseable `started_at` must
    still honor --since via the file mtime fallback. Otherwise missing-
    timestamp runs sneak through incremental imports."""
    import os
    import time

    home = _plant_hermes_home(
        tmp_path,
        skills=[
            {"name": "skill-a", "provenance": "agent-created"},
            {"name": "skill-b", "provenance": "agent-created"},
        ],
        curator_runs=[
            {
                "run_id": "run-no-ts",
                "data": {
                    "provider": "anthropic",
                    "model": "claude-sonnet-4-5",
                    "consolidated": ["skill-a"],
                },
            },
            {
                "run_id": "run-no-ts-new",
                "data": {
                    "provider": "anthropic",
                    "model": "claude-sonnet-4-5",
                    "consolidated": ["skill-b"],
                },
            },
        ],
    )
    # Backdate the first run's run.json mtime so it falls before --since.
    old_path = home / "logs" / "curator" / "run-no-ts" / "run.json"
    old_ts = time.mktime(time.strptime("2026-05-01T00:00:00", "%Y-%m-%dT%H:%M:%S"))
    os.utime(old_path, (old_ts, old_ts))

    output = tmp_path / "out.jsonl"
    export_curator_decisions(home=home, output=output, since="2026-05-10T00:00:00Z")
    rows = _load_jsonl(output)
    target_names = {r["input"]["skill_name"] for r in rows}
    assert "skill-a" not in target_names
    assert "skill-b" in target_names
