"""AC-697 mission control-plane workflows (slice 3).

Mirrors ``ts/src/mission/control-plane.ts`` (slice 3). High-level
operator-facing helpers used by the CLI surface that lands in
slices 4 + 5:

- ``mission_checkpoint_dir`` returns the canonical checkpoint
  directory for a mission under ``<runs_root>/missions/<id>/checkpoints``.
- ``require_mission`` raises a clear error when the mission id is
  missing so the CLI does not later choke on a None.
- ``build_mission_status_payload`` / ``build_mission_result_payload``
  / ``build_mission_artifacts_payload`` return JSON-serialisable
  dicts so the CLI can emit them under ``--json`` without
  reformatting downstream.
- ``write_mission_checkpoint`` writes a checkpoint to the canonical
  directory and returns the file path.
- ``build_fallback_verifier`` rebuilds the same subgoal-coverage
  fallback the TS `control-plane.ts` uses when the mission has no
  registered verifier.
- ``run_mission_loop`` is the legacy mission loop (subgoal-stepping
  + verifier). Adaptive LLM-driven mode lands in a follow-up slice
  once the package-wide LLM provider plumbing is in place.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from autocontext.mission.checkpoint import save_checkpoint
from autocontext.mission.executor import (
    RunUntilDoneResult,
    StepResult,
    run_until_done,
)
from autocontext.mission.types import Mission, MissionStatus, VerifierResult
from autocontext.mission.verifiers import rehydrate_mission_verifier

if TYPE_CHECKING:
    from autocontext.mission.manager import MissionManager


__all__ = [
    "build_fallback_verifier",
    "build_mission_artifacts_payload",
    "build_mission_result_payload",
    "build_mission_status_payload",
    "mission_checkpoint_dir",
    "require_mission",
    "run_mission_loop",
    "write_mission_checkpoint",
]


def mission_checkpoint_dir(runs_root: str | Path, mission_id: str) -> str:
    return str(Path(runs_root) / "missions" / mission_id / "checkpoints")


def require_mission(manager: MissionManager, mission_id: str) -> Mission:
    mission = manager.get(mission_id)
    if mission is None:
        raise ValueError(f"Mission not found: {mission_id}")
    return mission


def _model_dump(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return value


def build_mission_status_payload(manager: MissionManager, mission_id: str) -> dict[str, Any]:
    mission = require_mission(manager, mission_id)
    steps = manager.steps(mission_id)
    subgoals = manager.subgoals(mission_id)
    verifications = manager.verifications(mission_id)
    payload: dict[str, Any] = dict(_model_dump(mission))
    payload["stepsCount"] = len(steps)
    payload["subgoalCount"] = len(subgoals)
    payload["verificationCount"] = len(verifications)
    payload["budgetUsage"] = _model_dump(manager.budget_usage(mission_id))
    payload["latestVerification"] = _model_dump(verifications[-1]) if verifications else None
    return payload


def build_mission_result_payload(manager: MissionManager, mission_id: str) -> dict[str, Any]:
    mission = require_mission(manager, mission_id)
    steps = manager.steps(mission_id)
    subgoals = manager.subgoals(mission_id)
    verifications = manager.verifications(mission_id)
    return {
        "mission": _model_dump(mission),
        "steps": [_model_dump(s) for s in steps],
        "subgoals": [_model_dump(s) for s in subgoals],
        "verifications": [_model_dump(v) for v in verifications],
        "budgetUsage": _model_dump(manager.budget_usage(mission_id)),
        "latestVerification": (_model_dump(verifications[-1]) if verifications else None),
    }


def build_mission_artifacts_payload(manager: MissionManager, mission_id: str, runs_root: str | Path) -> dict[str, Any]:
    mission = require_mission(manager, mission_id)
    checkpoint_dir = mission_checkpoint_dir(runs_root, mission_id)
    checkpoints: list[dict[str, Any]] = []
    cp_dir_path = Path(checkpoint_dir)
    if cp_dir_path.is_dir():
        for entry in sorted(cp_dir_path.iterdir(), reverse=True):
            if not entry.name.endswith(".json"):
                continue
            stats = entry.stat()
            mtime = datetime.fromtimestamp(stats.st_mtime, tz=UTC).isoformat().replace("+00:00", "Z")
            checkpoints.append(
                {
                    "name": entry.name,
                    "path": str(entry),
                    "sizeBytes": stats.st_size,
                    "updatedAt": mtime,
                }
            )
    latest_checkpoint: dict[str, Any] | None = None
    if checkpoints:
        latest_checkpoint = _load_checkpoint_payload(Path(checkpoints[0]["path"]))
    return {
        "missionId": mission.id,
        "status": mission.status,
        "checkpointDir": checkpoint_dir,
        "checkpoints": checkpoints,
        "latestCheckpoint": latest_checkpoint,
    }


def _load_checkpoint_payload(path: Path) -> dict[str, Any]:
    import json

    loaded = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise ValueError(f"checkpoint payload at {path} is not a JSON object")
    return loaded


def write_mission_checkpoint(manager: MissionManager, mission_id: str, runs_root: str | Path) -> str:
    require_mission(manager, mission_id)
    return save_checkpoint(
        manager._store,  # noqa: SLF001 — control-plane intentionally reaches into the store
        mission_id,
        mission_checkpoint_dir(runs_root, mission_id),
    )


def build_fallback_verifier(manager: MissionManager, mission_id: str) -> Callable[[str], VerifierResult]:
    """Returns a sync verifier that passes iff every subgoal is in a
    terminal state, fails with the remaining subgoals listed
    otherwise, and falls back to "no verifier registered" when the
    mission has no subgoals at all. Mirrors TS ``buildFallbackVerifier``.
    """

    def _fallback(_mid: str) -> VerifierResult:
        subgoals = manager.subgoals(mission_id)
        if not subgoals:
            return VerifierResult(
                passed=False,
                reason="No verifier registered",
                suggestions=(),
                metadata={"autoVerifier": "none"},
            )
        remaining = [sg for sg in subgoals if sg.status not in ("completed", "skipped")]
        if not remaining:
            return VerifierResult(
                passed=True,
                reason="All subgoals completed",
                suggestions=(),
                metadata={"autoVerifier": "subgoals"},
            )
        suggestions = tuple(sg.description for sg in remaining[:3])
        return VerifierResult(
            passed=False,
            reason=f"{len(remaining)} subgoal(s) remaining",
            suggestions=suggestions,
            metadata={
                "autoVerifier": "subgoals",
                "remainingSubgoalIds": [sg.id for sg in remaining],
            },
        )

    return _fallback


def run_mission_loop(
    manager: MissionManager,
    mission_id: str,
    runs_root: str | Path,
    *,
    max_iterations: int = 1,
    step_description: str | None = None,
) -> dict[str, Any]:
    """Run the mission loop until verifier-pass / budget-exhaust /
    blocked / max_iterations. Mirrors TS legacy loop; the adaptive
    LLM-driven branch lands in a follow-up slice once the
    package-wide LLM provider plumbing is in place.

    Side effects: writes a checkpoint to the canonical
    ``mission_checkpoint_dir`` path before returning so the CLI can
    surface it as a stable artifact reference.
    """
    mission = require_mission(manager, mission_id)
    metadata = mission.metadata
    mission_type = metadata.get("missionType")

    if not manager.has_verifier(mission_id):
        rehydrate_mission_verifier(manager, mission)

    if not manager.has_verifier(mission_id):
        manager.set_verifier(mission_id, build_fallback_verifier(manager, mission_id))

    loop_result = _run_legacy_mission_loop(
        manager,
        mission_id,
        mission.goal,
        max_iterations=max_iterations,
        step_description=step_description,
    )

    latest_verifications = manager.verifications(mission_id)
    latest_verification = latest_verifications[-1] if latest_verifications else None
    final_status: MissionStatus = loop_result.final_status
    if (
        mission_type == "code"
        and latest_verification is not None
        and latest_verification.passed is False
        and loop_result.final_status == "active"
    ):
        manager.set_status(mission_id, "failed")
        final_status = "failed"

    checkpoint_path = write_mission_checkpoint(manager, mission_id, runs_root)
    return {
        "id": mission_id,
        "finalStatus": final_status,
        "stepsExecuted": loop_result.steps_executed,
        "verifierPassed": loop_result.verifier_passed,
        "latestVerification": (_model_dump(latest_verification) if latest_verification is not None else None),
        "checkpointPath": checkpoint_path,
    }


def _run_legacy_mission_loop(
    manager: MissionManager,
    mission_id: str,
    goal: str,
    *,
    max_iterations: int,
    step_description: str | None,
) -> RunUntilDoneResult:
    iteration = 0
    explicit_description = step_description.strip() if step_description and step_description.strip() else None

    def _executor(current_mission_id: str) -> StepResult:
        nonlocal iteration
        iteration += 1
        next_subgoal = next(
            (sg for sg in manager.subgoals(current_mission_id) if sg.status in ("pending", "active")),
            None,
        )
        if next_subgoal is not None:
            manager.update_subgoal_status(next_subgoal.id, "completed")
            return StepResult(
                description=f"Completed subgoal: {next_subgoal.description}",
                status="completed",
            )

        if explicit_description is not None:
            description = explicit_description
        elif max_iterations == 1:
            description = f"Advance mission toward goal: {goal}"
        else:
            description = f"Advance mission toward goal ({iteration}/{max_iterations}): {goal}"
        return StepResult(description=description, status="completed")

    return run_until_done(manager, mission_id, _executor, max_iterations=max_iterations)
