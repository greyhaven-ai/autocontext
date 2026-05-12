"""Path safety tests for scenario-scoped artifacts."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from autocontext.knowledge.normalized_metrics import (
    CostEfficiency,
    NormalizedProgress,
    RunProgressReport,
)
from autocontext.knowledge.weakness import WeaknessReport
from autocontext.storage.artifacts import ArtifactStore

INVALID_SCENARIO_NAMES = [".", "..", "../outside", "nested/name", r"nested\name", r"..\outside"]


def _make_store(tmp_path: Path) -> ArtifactStore:
    return ArtifactStore(
        runs_root=tmp_path / "runs",
        knowledge_root=tmp_path / "knowledge",
        skills_root=tmp_path / "skills",
        claude_skills_path=tmp_path / ".claude" / "skills",
    )


def _progress_report() -> RunProgressReport:
    return RunProgressReport(
        run_id="run_1",
        scenario="grid_ctf",
        total_generations=2,
        advances=1,
        rollbacks=1,
        retries=0,
        progress=NormalizedProgress(raw_score=0.8, normalized_score=0.8, pct_of_ceiling=80.0),
        cost=CostEfficiency(total_tokens=100),
    )


def _weakness_report() -> WeaknessReport:
    return WeaknessReport(run_id="run_1", scenario="grid_ctf", total_generations=2, weaknesses=[])


def test_scenario_scoped_artifact_methods_accept_normal_names(tmp_path: Path) -> None:
    store = _make_store(tmp_path)

    store.write_playbook("grid_ctf", "## Playbook")
    store.write_hints("grid_ctf", "Scout borders.")
    store.append_dead_end("grid_ctf", "Do not repeat the failed opening.")
    store.write_progress_report("grid_ctf", "run_1", _progress_report())
    store.write_weakness_report("grid_ctf", "run_1", _weakness_report())
    store.persist_skill_note("grid_ctf", 1, "advance", "Keep center control.")
    store.replace_skill_lessons("grid_ctf", ["- Consolidated skill lesson"])

    assert "Playbook" in store.read_playbook("grid_ctf")
    assert store.read_hints("grid_ctf") == "Scout borders.\n"
    assert "failed opening" in store.read_dead_ends("grid_ctf")
    assert isinstance(store.read_progress_report("grid_ctf", "run_1"), RunProgressReport)
    assert isinstance(store.read_weakness_report("grid_ctf", "run_1"), WeaknessReport)
    assert store.read_skill_lessons_raw("grid_ctf") == ["- Consolidated skill lesson"]
    assert "Consolidated skill lesson" in store.read_skills("grid_ctf")


@pytest.mark.parametrize("scenario_name", INVALID_SCENARIO_NAMES)
@pytest.mark.parametrize(
    ("_method_name", "write_call"),
    [
        ("write_playbook", lambda store, scenario_name: store.write_playbook(scenario_name, "## Playbook")),
        ("write_hints", lambda store, scenario_name: store.write_hints(scenario_name, "Scout borders.")),
        ("append_dead_end", lambda store, scenario_name: store.append_dead_end(scenario_name, "Bad opening.")),
        (
            "write_progress_report",
            lambda store, scenario_name: store.write_progress_report(scenario_name, "run_1", _progress_report()),
        ),
        (
            "write_weakness_report",
            lambda store, scenario_name: store.write_weakness_report(scenario_name, "run_1", _weakness_report()),
        ),
        (
            "persist_skill_note",
            lambda store, scenario_name: store.persist_skill_note(scenario_name, 1, "advance", "Unsafe lesson"),
        ),
        (
            "replace_skill_lessons",
            lambda store, scenario_name: store.replace_skill_lessons(scenario_name, ["- Unsafe lesson"]),
        ),
    ],
)
def test_scenario_scoped_writes_reject_unsafe_names(
    tmp_path: Path,
    scenario_name: str,
    _method_name: str,
    write_call: Callable[[ArtifactStore, str], None],
) -> None:
    store = _make_store(tmp_path)

    with pytest.raises(ValueError, match="single path segment"):
        write_call(store, scenario_name)

    assert list((tmp_path / "knowledge").rglob("*")) == []
    assert list((tmp_path / "skills").rglob("*")) == []
    assert not list(tmp_path.glob("*.md"))
    assert not list(tmp_path.glob("*.json"))
    assert not (tmp_path / "outside-ops").exists()


@pytest.mark.parametrize("scenario_name", INVALID_SCENARIO_NAMES)
@pytest.mark.parametrize(
    ("_method_name", "read_call"),
    [
        ("read_playbook", lambda store, scenario_name: store.read_playbook(scenario_name)),
        ("read_hints", lambda store, scenario_name: store.read_hints(scenario_name)),
        ("read_dead_ends", lambda store, scenario_name: store.read_dead_ends(scenario_name)),
        ("read_progress_report", lambda store, scenario_name: store.read_progress_report(scenario_name, "run_1")),
        ("read_weakness_report", lambda store, scenario_name: store.read_weakness_report(scenario_name, "run_1")),
        ("read_skills", lambda store, scenario_name: store.read_skills(scenario_name)),
        ("read_skill_lessons_raw", lambda store, scenario_name: store.read_skill_lessons_raw(scenario_name)),
    ],
)
def test_scenario_scoped_reads_reject_unsafe_names(
    tmp_path: Path,
    scenario_name: str,
    _method_name: str,
    read_call: Callable[[ArtifactStore, str], object],
) -> None:
    store = _make_store(tmp_path)

    with pytest.raises(ValueError, match="single path segment"):
        read_call(store, scenario_name)
