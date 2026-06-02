"""AC-697 mission verifiers (slice 2).

Mirrors ``ts/src/mission/verifiers.ts`` (AC-415). Hard external
verifiers that run shell commands (test, lint, build) and decide
mission success from the exit code.

- ``CommandVerifier`` runs a single shell command via ``/bin/sh -c``;
  exit 0 = pass.
- ``CompositeVerifier`` short-circuits on the first failing verifier
  (matches the TS behaviour).
- ``CodeMissionSpec`` is the Pydantic spec for the
  ``create_code_mission`` factory; required + optional commands are
  composed into a single ``Verifier``.
- ``create_code_mission`` registers the mission via the manager and
  attaches the verifier.
- ``rehydrate_mission_verifier`` rebinds the verifier from the
  mission's stored ``metadata`` so a process restart picks the
  verifier up without re-running ``create_code_mission``.
"""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING, Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, StrictStr

from autocontext.mission.types import Mission, MissionBudget, VerifierResult

if TYPE_CHECKING:
    from autocontext.mission.manager import MissionManager

__all__ = [
    "CodeMissionSpec",
    "CommandVerifier",
    "CompositeVerifier",
    "Verifier",
    "attach_code_mission_verifier",
    "create_code_mission",
    "rehydrate_mission_verifier",
]


_COMMAND_TIMEOUT_SECONDS = 120
_STDERR_SUGGESTION_LIMIT = 500
_OUTPUT_METADATA_LIMIT = 2000


class Verifier(Protocol):
    """Mirrors TS `Verifier` interface."""

    label: str

    def verify(self, mission_id: str) -> VerifierResult: ...


class CommandVerifier:
    """Runs a single shell command via ``/bin/sh -c`` and reports the
    result. Mirrors TS ``CommandVerifier`` shape: exit 0 -> pass;
    non-zero exit (or timeout / spawn failure) -> fail with stderr
    snippet surfaced as a suggestion."""

    def __init__(self, command: str, cwd: str) -> None:
        self._command = command
        self._cwd = cwd
        self.label = command

    def verify(self, _mission_id: str) -> VerifierResult:
        try:
            proc = subprocess.run(
                ["/bin/sh", "-c", self._command],
                cwd=self._cwd,
                capture_output=True,
                text=True,
                timeout=_COMMAND_TIMEOUT_SECONDS,
                check=False,
            )
        except subprocess.TimeoutExpired as err:
            return VerifierResult(
                passed=False,
                reason=f"Command {self._command!r} timed out after {_COMMAND_TIMEOUT_SECONDS}s",
                suggestions=(),
                metadata={
                    "command": self._command,
                    "timeout": True,
                    "exitCode": -1,
                    "stdout": _truncate(_coerce_str(err.stdout), _OUTPUT_METADATA_LIMIT),
                    "stderr": _truncate(_coerce_str(err.stderr), _OUTPUT_METADATA_LIMIT),
                },
            )
        except OSError as err:
            return VerifierResult(
                passed=False,
                reason=f"Command {self._command!r} could not start: {err}",
                suggestions=(),
                metadata={
                    "command": self._command,
                    "spawnFailed": True,
                },
            )

        if proc.returncode == 0:
            return VerifierResult(
                passed=True,
                reason=f"Command '{self._command}' passed (exit 0)",
                suggestions=(),
                metadata={"stdout": proc.stdout.strip(), "command": self._command},
            )

        stderr = proc.stderr or ""
        suggestions: tuple[str, ...] = ()
        if stderr.strip():
            suggestions = (f"stderr: {stderr.strip()[:_STDERR_SUGGESTION_LIMIT]}",)
        return VerifierResult(
            passed=False,
            reason=f"Command '{self._command}' failed (exit {proc.returncode})",
            suggestions=suggestions,
            metadata={
                "command": self._command,
                "exitCode": proc.returncode,
                "stdout": _truncate(proc.stdout or "", _OUTPUT_METADATA_LIMIT),
                "stderr": _truncate(stderr, _OUTPUT_METADATA_LIMIT),
            },
        )


class CompositeVerifier:
    """All-must-pass composite. Mirrors TS short-circuit shape:
    iterate verifiers in declaration order, return the first
    failure with `failedVerifier` set on the metadata; otherwise
    aggregate to a single passing result with the verifier count."""

    def __init__(self, verifiers: list[Verifier]) -> None:
        self._verifiers = list(verifiers)
        self.label = " && ".join(v.label for v in verifiers)

    def verify(self, mission_id: str) -> VerifierResult:
        for verifier in self._verifiers:
            result = verifier.verify(mission_id)
            if not result.passed:
                metadata = dict(result.metadata)
                metadata["failedVerifier"] = verifier.label
                return VerifierResult(
                    passed=False,
                    reason=result.reason,
                    suggestions=result.suggestions,
                    metadata=metadata,
                )
        return VerifierResult(
            passed=True,
            reason=f"All {len(self._verifiers)} verifier(s) passed",
            suggestions=(),
            metadata={"verifierCount": len(self._verifiers)},
        )


class CodeMissionSpec(BaseModel):
    """Pydantic v2 mirror of TS ``CodeMissionSpecSchema``.

    ``extra="forbid"`` so a misspelled field rejects at parse time;
    primitive coercion blocked via Strict types (PR #1014 review P2
    parity)."""

    model_config = ConfigDict(extra="forbid")
    name: StrictStr
    goal: StrictStr
    repo_path: StrictStr
    test_command: StrictStr
    lint_command: StrictStr | None = None
    build_command: StrictStr | None = None
    budget: MissionBudget | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


def _build_code_mission_verifier(spec: CodeMissionSpec) -> Verifier:
    verifiers: list[Verifier] = [CommandVerifier(spec.test_command, spec.repo_path)]
    if spec.lint_command:
        verifiers.append(CommandVerifier(spec.lint_command, spec.repo_path))
    if spec.build_command:
        verifiers.append(CommandVerifier(spec.build_command, spec.repo_path))
    if len(verifiers) == 1:
        return verifiers[0]
    return CompositeVerifier(verifiers)


def attach_code_mission_verifier(manager: MissionManager, mission_id: str, spec: CodeMissionSpec) -> None:
    verifier = _build_code_mission_verifier(spec)
    manager.set_verifier(mission_id, verifier.verify)


def rehydrate_mission_verifier(manager: MissionManager, mission: Mission) -> bool:
    """Rebind a verifier from the mission's stored ``metadata`` blob.

    Returns True when the metadata carries enough state to rebuild
    the verifier (mission_type == "code" + repo_path + test_command);
    False otherwise. Optional commands ride through when present.
    """
    metadata = mission.metadata
    if metadata.get("missionType") != "code":
        return False

    repo_path = metadata.get("repoPath")
    test_command = metadata.get("testCommand")
    if not isinstance(repo_path, str) or not isinstance(test_command, str):
        return False

    lint = metadata.get("lintCommand")
    build = metadata.get("buildCommand")
    attach_code_mission_verifier(
        manager,
        mission.id,
        CodeMissionSpec(
            name=mission.name,
            goal=mission.goal,
            repo_path=repo_path,
            test_command=test_command,
            lint_command=lint if isinstance(lint, str) else None,
            build_command=build if isinstance(build, str) else None,
        ),
    )
    return True


def create_code_mission(manager: MissionManager, spec: CodeMissionSpec) -> str:
    """Mirror TS ``createCodeMission``: register the mission with
    ``missionType == "code"`` plus the verifier commands on the
    metadata blob, then attach the matching verifier."""
    parsed = CodeMissionSpec.model_validate(spec.model_dump())
    metadata: dict[str, Any] = {
        **parsed.metadata,
        "missionType": "code",
        "repoPath": parsed.repo_path,
        "testCommand": parsed.test_command,
    }
    if parsed.lint_command is not None:
        metadata["lintCommand"] = parsed.lint_command
    if parsed.build_command is not None:
        metadata["buildCommand"] = parsed.build_command

    mission_id = manager.create(
        name=parsed.name,
        goal=parsed.goal,
        budget=parsed.budget,
        metadata=metadata,
    )
    attach_code_mission_verifier(manager, mission_id, parsed)
    return mission_id


def _truncate(text: str, limit: int) -> str:
    text = text.strip()
    return text[:limit]


def _coerce_str(value: bytes | str | None) -> str:
    """``subprocess.TimeoutExpired.stdout`` / ``stderr`` are typed as
    ``bytes | str | None`` even with ``text=True``. Coerce to ``str``
    so the downstream ``_truncate`` call is type-safe."""
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value
