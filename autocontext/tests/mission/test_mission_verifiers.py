"""AC-697 mission verifier tests (slice 2).

Mirrors the unit-test surface for ``ts/src/mission/verifiers.ts``.
Covers CommandVerifier exit-0 / exit-non-zero / stderr-into-suggestion,
CompositeVerifier short-circuit, the CodeMissionSpec strict schema,
``create_code_mission`` factory, and rehydration from stored metadata.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from autocontext.mission import (
    CodeMissionSpec,
    CommandVerifier,
    CompositeVerifier,
    MissionManager,
    VerifierResult,
    create_code_mission,
    rehydrate_mission_verifier,
)

# ---------------------------------------------------------------------------
# CommandVerifier
# ---------------------------------------------------------------------------


def test_command_verifier_passes_on_exit_zero(tmp_path: Path) -> None:
    cv = CommandVerifier("true", str(tmp_path))
    result = cv.verify("mission-x")
    assert result.passed is True
    assert "exit 0" in result.reason
    assert result.metadata["command"] == "true"


def test_command_verifier_fails_on_non_zero_exit_with_stderr_suggestion(
    tmp_path: Path,
) -> None:
    cv = CommandVerifier("echo boom 1>&2; false", str(tmp_path))
    result = cv.verify("mission-x")
    assert result.passed is False
    assert "exit 1" in result.reason
    assert any("stderr: boom" in s for s in result.suggestions)
    assert result.metadata["exitCode"] == 1


def test_command_verifier_label_is_the_command(tmp_path: Path) -> None:
    cv = CommandVerifier("ls -la", str(tmp_path))
    assert cv.label == "ls -la"


# ---------------------------------------------------------------------------
# CompositeVerifier
# ---------------------------------------------------------------------------


class _StaticVerifier:
    def __init__(self, label: str, result: VerifierResult) -> None:
        self.label = label
        self._result = result

    def verify(self, _mission_id: str) -> VerifierResult:
        return self._result


def test_composite_verifier_passes_when_all_inner_pass() -> None:
    cv = CompositeVerifier(
        [
            _StaticVerifier("a", VerifierResult(passed=True, reason="a")),
            _StaticVerifier("b", VerifierResult(passed=True, reason="b")),
        ]
    )
    result = cv.verify("mission-x")
    assert result.passed is True
    assert result.metadata["verifierCount"] == 2


def test_composite_verifier_short_circuits_on_first_failure() -> None:
    cv = CompositeVerifier(
        [
            _StaticVerifier("a", VerifierResult(passed=False, reason="a failed")),
            _StaticVerifier("b", VerifierResult(passed=True, reason="b ok")),
        ]
    )
    result = cv.verify("mission-x")
    assert result.passed is False
    assert result.reason == "a failed"
    assert result.metadata["failedVerifier"] == "a"


def test_composite_verifier_label_joins_inner_labels() -> None:
    cv = CompositeVerifier(
        [
            _StaticVerifier("first", VerifierResult(passed=True, reason="")),
            _StaticVerifier("second", VerifierResult(passed=True, reason="")),
        ]
    )
    assert cv.label == "first && second"


# ---------------------------------------------------------------------------
# CodeMissionSpec schema strictness
# ---------------------------------------------------------------------------


def test_code_mission_spec_extra_keys_rejected() -> None:
    """Slice 1 strictness applied to slice 2: a typo in the spec
    rejects rather than being silently dropped."""
    with pytest.raises(ValidationError):
        CodeMissionSpec.model_validate(
            {
                "name": "x",
                "goal": "g",
                "repo_path": "/tmp",
                "test_command": "true",
                "lint_commands": "oops",  # typo
            }
        )


def test_code_mission_spec_rejects_string_for_priority_via_strict_str() -> None:
    """Primitive coercion off across the spec."""
    with pytest.raises(ValidationError):
        CodeMissionSpec.model_validate(
            {
                "name": 5,  # int, not str
                "goal": "g",
                "repo_path": "/tmp",
                "test_command": "true",
            }
        )


# ---------------------------------------------------------------------------
# create_code_mission factory
# ---------------------------------------------------------------------------


def test_create_code_mission_registers_metadata_and_verifier(tmp_path: Path) -> None:
    with MissionManager(str(tmp_path / "m.sqlite3")) as mgr:
        mid = create_code_mission(
            mgr,
            CodeMissionSpec(
                name="ship login",
                goal="OAuth passes",
                repo_path=str(tmp_path),
                test_command="true",
                lint_command="true",
            ),
        )
        mission = mgr.get(mid)
        assert mission is not None
        assert mission.metadata["missionType"] == "code"
        assert mission.metadata["repoPath"] == str(tmp_path)
        assert mission.metadata["testCommand"] == "true"
        assert mission.metadata["lintCommand"] == "true"
        assert mgr.has_verifier(mid) is True


def test_create_code_mission_test_pass_completes_mission(tmp_path: Path) -> None:
    with MissionManager(str(tmp_path / "m.sqlite3")) as mgr:
        mid = create_code_mission(
            mgr,
            CodeMissionSpec(
                name="x",
                goal="g",
                repo_path=str(tmp_path),
                test_command="true",
            ),
        )
        result = mgr.verify(mid)
        assert result.passed is True
        assert mgr.get(mid).status == "completed"


# ---------------------------------------------------------------------------
# rehydrate_mission_verifier
# ---------------------------------------------------------------------------


def test_rehydrate_attaches_verifier_when_metadata_carries_state(
    tmp_path: Path,
) -> None:
    """Slice-3 control-plane will call this on process restart; the
    helper must rebind the verifier without re-running
    `create_code_mission`."""
    with MissionManager(str(tmp_path / "m.sqlite3")) as mgr:
        mid = mgr.create(
            name="x",
            goal="g",
            metadata={
                "missionType": "code",
                "repoPath": str(tmp_path),
                "testCommand": "true",
            },
        )
        mission = mgr.get(mid)
        assert mission is not None
        assert mgr.has_verifier(mid) is False
        assert rehydrate_mission_verifier(mgr, mission) is True
        assert mgr.has_verifier(mid) is True


def test_rehydrate_returns_false_when_metadata_missing(tmp_path: Path) -> None:
    with MissionManager(str(tmp_path / "m.sqlite3")) as mgr:
        mid = mgr.create(name="x", goal="g", metadata={"missionType": "code"})
        mission = mgr.get(mid)
        assert mission is not None
        # missing repoPath + testCommand -> rehydrate refuses
        assert rehydrate_mission_verifier(mgr, mission) is False
        assert mgr.has_verifier(mid) is False
