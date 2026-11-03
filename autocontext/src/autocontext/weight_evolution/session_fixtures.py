"""Trusted, stateless inspect/edit/probe responses for offline session fixtures.

The catalog contains literal responses, never an answer generator or executable
code. Its identity excludes full request hashes to avoid a cycle through the
session's adapter pin; each entry instead binds the operation and candidate.
This is a logical fixture boundary, not an untrusted-code security sandbox.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Literal, Self

from pydantic import Field, model_validator

from autocontext.weight_evolution.artifacts import canonical_digest, load_small_json
from autocontext.weight_evolution.models import Digest, FrozenModel
from autocontext.weight_evolution.session_models import AdapterResult, ProbeOutput, SessionAdapterRequest

MAX_SESSION_SCRIPT_BYTES = 8 * 1024 * 1024


class FixtureAdapterError(RuntimeError):
    """A scripted known failure; the controller must retain its reservation."""


class FixtureAdapterInterrupted(KeyboardInterrupt):
    """Simulate an interrupted dispatch without a terminal result or retry."""


class SessionFixtureEntry(FrozenModel):
    operation_digest: Digest
    candidate_digest: Digest
    operation_kind: Literal["inspect", "edit", "probe"]
    outcome: Literal["return", "failure", "interruption"] = "return"
    message: str = Field(default="", max_length=4096)
    probe_outputs: tuple[ProbeOutput, ...] = Field(default=(), max_length=1000)
    simulated_seconds: float = Field(default=0.0, ge=0, le=86400, allow_inf_nan=False)

    @model_validator(mode="after")
    def result_shape(self) -> Self:
        if self.outcome != "return" or self.operation_kind != "probe":
            if self.probe_outputs:
                raise ValueError("only successful probe entries may contain responses")
        elif not self.probe_outputs:
            raise ValueError("successful probe entries require prerecorded responses")
        ids = [sample.problem_id for sample in self.probe_outputs]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate prerecorded probe problem")
        return self


class SessionFixtureScript(FrozenModel):
    schema_version: Literal[1] = 1
    execution_kind: Literal["fixture"] = "fixture"
    model_execution_performed: Literal[False] = False
    gpu_execution_deferred: Literal[True] = True
    entries: tuple[SessionFixtureEntry, ...] = Field(min_length=1, max_length=10000)

    @model_validator(mode="after")
    def unique_operations(self) -> Self:
        if len({item.operation_digest for item in self.entries}) != len(self.entries):
            raise ValueError("duplicate scripted operation")
        return self

    @property
    def digest(self) -> str:
        return canonical_digest(self.to_dict())


def load_session_fixture_script(path: Path) -> SessionFixtureScript:
    """Read bounded, regular local JSON only; source code is never imported."""
    value = load_small_json(path, max_bytes=MAX_SESSION_SCRIPT_BYTES)
    return SessionFixtureScript.model_validate_json(json.dumps(value, allow_nan=False))


class ScriptedSessionAdapter:
    """Replay an authenticated catalog without model calls or mutable state."""

    execution_kind = "fixture"

    def __init__(self, script: SessionFixtureScript) -> None:
        # Revalidate even programmatically constructed/copied models before use.
        self._script = SessionFixtureScript.model_validate_json(json.dumps(script.to_dict(), allow_nan=False))

    @property
    def script(self) -> SessionFixtureScript:
        return self._script

    def provenance(self) -> dict[str, Any]:
        package = Path(__file__).parent
        source = {name: hashlib.sha256((package / name).read_bytes()).hexdigest()
                  for name in ("session_fixtures.py", "session_models.py", "comparison_models.py")}
        return {"schema_version": 1, "adapter": "scripted-session-fixture-v1",
                "execution_kind": "fixture", "model_execution_performed": False,
                "gpu_execution_deferred": True, "script_digest": self._script.digest,
                "implementation_sources": source,
                "response_origin": "literal_prerecorded_catalog_not_generated_from_gold"}

    @property
    def identity_digest(self) -> str:
        return canonical_digest(self.provenance())

    def execute(self, request: SessionAdapterRequest) -> AdapterResult:
        """Return one fixed result, known failure, or ambiguous interruption."""
        request = SessionAdapterRequest.model_validate_json(json.dumps(request.to_dict(), allow_nan=False))
        entries = {entry.operation_digest: entry for entry in self._script.entries}
        entry = entries.get(request.operation.digest)
        if entry is None:
            raise FixtureAdapterError("operation is absent from the pinned fixture script")
        if (entry.candidate_digest != request.candidate.digest or entry.operation_kind != request.operation.kind):
            raise FixtureAdapterError("scripted candidate or operation kind differs from request")
        if entry.outcome == "failure":
            raise FixtureAdapterError(entry.message or "scripted known adapter failure")
        if entry.outcome == "interruption":
            raise FixtureAdapterInterrupted(entry.message or "scripted ambiguous adapter interruption")
        if request.operation.kind == "probe":
            expected = request.operation.problem_ids
            if (tuple(problem.problem_id for problem in request.practice) != expected
                    or tuple(sample.problem_id for sample in entry.probe_outputs) != expected):
                raise FixtureAdapterError("prerecorded responses differ from the selected practice projections")
        return AdapterResult(operation_digest=request.operation.digest, message=entry.message,
                             probe_outputs=entry.probe_outputs, simulated_seconds=entry.simulated_seconds)
