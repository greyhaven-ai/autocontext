"""AC-697 mission control-plane tests (slice 3).

Mirrors ``ts/src/mission/control-plane.ts`` test surface: status /
result / artifact payload shape; checkpoint write; fallback verifier
behaviour; legacy run_mission_loop with subgoals + max_iterations +
code-mission failing-verifier downgrade-to-failed branch.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from autocontext.mission import (
    CodeMissionSpec,
    MissionBudget,
    MissionManager,
    VerifierResult,
    build_fallback_verifier,
    build_mission_artifacts_payload,
    build_mission_result_payload,
    build_mission_status_payload,
    create_code_mission,
    mission_checkpoint_dir,
    require_mission,
    run_mission_loop,
    write_mission_checkpoint,
)


def _manager(tmp_path: Path) -> MissionManager:
    return MissionManager(str(tmp_path / "m.sqlite3"))


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def test_mission_checkpoint_dir_uses_canonical_layout(tmp_path: Path) -> None:
    path = mission_checkpoint_dir(tmp_path, "mission-abc")
    assert path == str(tmp_path / "missions" / "mission-abc" / "checkpoints")


def test_require_mission_raises_for_unknown_id(tmp_path: Path) -> None:
    with _manager(tmp_path) as mgr:
        with pytest.raises(ValueError, match="Mission not found"):
            require_mission(mgr, "mission-nope")


# ---------------------------------------------------------------------------
# Payloads
# ---------------------------------------------------------------------------


def test_status_payload_carries_counts_and_latest_verification(
    tmp_path: Path,
) -> None:
    with _manager(tmp_path) as mgr:
        mid = mgr.create(name="x", goal="g", budget=MissionBudget(max_steps=4))
        mgr.advance(mid, "s1")
        mgr.add_subgoal(mid, description="sg1")
        mgr.set_verifier(mid, lambda _mid: VerifierResult(passed=False, reason="not yet"))
        mgr.verify(mid)

        payload = build_mission_status_payload(mgr, mid)
        assert payload["id"] == mid
        assert payload["stepsCount"] == 1
        assert payload["subgoalCount"] == 1
        assert payload["verificationCount"] == 1
        assert payload["budgetUsage"]["steps_used"] == 1
        assert payload["latestVerification"]["reason"] == "not yet"


def test_result_payload_serialises_every_section(tmp_path: Path) -> None:
    with _manager(tmp_path) as mgr:
        mid = mgr.create(name="x", goal="g")
        mgr.add_subgoal(mid, description="sg1")
        payload = build_mission_result_payload(mgr, mid)
        assert payload["mission"]["id"] == mid
        assert payload["steps"] == []
        assert payload["subgoals"][0]["description"] == "sg1"
        assert payload["latestVerification"] is None


def test_artifacts_payload_returns_empty_checkpoint_list_when_dir_missing(
    tmp_path: Path,
) -> None:
    with _manager(tmp_path) as mgr:
        mid = mgr.create(name="x", goal="g")
        payload = build_mission_artifacts_payload(mgr, mid, tmp_path / "runs")
        assert payload["missionId"] == mid
        assert payload["status"] == "active"
        assert payload["checkpoints"] == []
        assert payload["latestCheckpoint"] is None


def test_artifacts_payload_lists_checkpoints_newest_first(tmp_path: Path) -> None:
    import time

    with _manager(tmp_path) as mgr:
        mid = mgr.create(name="x", goal="g")
        first = write_mission_checkpoint(mgr, mid, tmp_path / "runs")
        # Checkpoint filenames embed unix-ms timestamps. A second
        # write inside the same ms would overwrite the first file.
        # Sleep 2ms so the second checkpoint gets a distinct name.
        time.sleep(0.002)
        mgr.advance(mid, "step")
        second = write_mission_checkpoint(mgr, mid, tmp_path / "runs")
        assert first != second

        payload = build_mission_artifacts_payload(mgr, mid, tmp_path / "runs")
        names = [c["name"] for c in payload["checkpoints"]]
        # Reverse-sorted file names; newest first.
        assert names == sorted(names, reverse=True)
        assert payload["latestCheckpoint"]["mission"]["id"] == mid


# ---------------------------------------------------------------------------
# Fallback verifier
# ---------------------------------------------------------------------------


def test_fallback_verifier_returns_no_verifier_when_no_subgoals(
    tmp_path: Path,
) -> None:
    with _manager(tmp_path) as mgr:
        mid = mgr.create(name="x", goal="g")
        verifier = build_fallback_verifier(mgr, mid)
        result = verifier(mid)
        assert result.passed is False
        assert result.reason == "No verifier registered"
        assert result.metadata["autoVerifier"] == "none"


def test_fallback_verifier_passes_when_every_subgoal_terminal(
    tmp_path: Path,
) -> None:
    with _manager(tmp_path) as mgr:
        mid = mgr.create(name="x", goal="g")
        sgid = mgr.add_subgoal(mid, description="finish")
        mgr.update_subgoal_status(sgid, "completed")
        verifier = build_fallback_verifier(mgr, mid)
        result = verifier(mid)
        assert result.passed is True
        assert result.metadata["autoVerifier"] == "subgoals"


def test_fallback_verifier_lists_remaining_subgoals(tmp_path: Path) -> None:
    with _manager(tmp_path) as mgr:
        mid = mgr.create(name="x", goal="g")
        mgr.add_subgoal(mid, description="A")
        mgr.add_subgoal(mid, description="B")
        verifier = build_fallback_verifier(mgr, mid)
        result = verifier(mid)
        assert result.passed is False
        assert "2 subgoal(s) remaining" in result.reason
        assert set(result.suggestions) == {"A", "B"}


# ---------------------------------------------------------------------------
# run_mission_loop
# ---------------------------------------------------------------------------


def test_run_mission_loop_uses_subgoal_executor_and_writes_checkpoint(
    tmp_path: Path,
) -> None:
    with _manager(tmp_path) as mgr:
        mid = mgr.create(name="x", goal="g")
        mgr.add_subgoal(mid, description="A")
        mgr.add_subgoal(mid, description="B")
        # No verifier registered -> fallback verifier kicks in; when
        # all subgoals complete it passes.
        result = run_mission_loop(mgr, mid, tmp_path / "runs", max_iterations=5)
        assert result["finalStatus"] == "completed"
        assert result["verifierPassed"] is True
        assert result["stepsExecuted"] == 2
        # Checkpoint was written.
        assert Path(result["checkpointPath"]).is_file()


def test_run_mission_loop_caps_at_max_iterations_with_no_subgoals(
    tmp_path: Path,
) -> None:
    """Without subgoals or a verifier, the fallback verifier reports
    `No verifier registered` and never passes; the loop should still
    return cleanly after `max_iterations` steps."""
    with _manager(tmp_path) as mgr:
        mid = mgr.create(name="x", goal="g")
        result = run_mission_loop(mgr, mid, tmp_path / "runs", max_iterations=2)
        assert result["stepsExecuted"] == 2
        assert result["finalStatus"] == "active"
        assert result["verifierPassed"] is False


def test_run_mission_loop_downgrades_code_mission_to_failed_when_verifier_fails(
    tmp_path: Path,
) -> None:
    """Code missions that the verifier rejects but the loop left
    `active` are downgraded to `failed` (TS parity branch)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    with _manager(tmp_path) as mgr:
        mid = create_code_mission(
            mgr,
            CodeMissionSpec(
                name="x",
                goal="g",
                repo_path=str(repo),
                test_command="false",  # always fails
            ),
        )
        result = run_mission_loop(mgr, mid, tmp_path / "runs", max_iterations=1)
        assert result["finalStatus"] == "failed"


def test_run_mission_loop_writes_checkpoint_under_canonical_dir(
    tmp_path: Path,
) -> None:
    with _manager(tmp_path) as mgr:
        mid = mgr.create(name="x", goal="g")
        result = run_mission_loop(mgr, mid, tmp_path / "runs", max_iterations=1)
        expected_dir = Path(mission_checkpoint_dir(tmp_path / "runs", mid))
        assert Path(result["checkpointPath"]).parent == expected_dir
        # The checkpoint is valid JSON with the canonical version field.
        payload = json.loads(Path(result["checkpointPath"]).read_text())
        assert payload["version"] == 1
        assert payload["mission"]["id"] == mid
