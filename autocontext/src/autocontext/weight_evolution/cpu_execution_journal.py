"""Versioned claim/outcome/check/result journal for measured CPU workflows.

The returned boundary outcome is published before callback validation. A claimed
operation is never dispatched again: saved outcomes can repair publication only;
missing outcomes retain uncertainty and the full operation reservation.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from autocontext.weight_evolution.artifacts import canonical_digest, file_identity, regular_file
from autocontext.weight_evolution.checkpoint_candidates import TensorCandidateRegistry
from autocontext.weight_evolution.cpu_execution_models import CpuExecutionContract
from autocontext.weight_evolution.cpu_execution_outcomes import (
    build_cpu_operation_check,
    build_cpu_operation_outcome,
    execution_runtime_identity,
    operation_requests,
    operation_value,
    validate_cpu_operation_check,
    validate_cpu_operation_outcome,
)
from autocontext.weight_evolution.cpu_measured_runner import run_measured_cpu_batch
from autocontext.weight_evolution.cpu_runner import CpuBatchReceipt
from autocontext.weight_evolution.cpu_search import _safe
from autocontext.weight_evolution.session import _immutable_json, _read_record, _same

METERS = ("proposals", "materializations", "inference_calls", "input_tokens", "output_tokens")
DIRECTORIES = ("claims", "outcomes", "checks", "results")
_STAGING = re.compile(r"^\.session-publish-[A-Za-z0-9_-]{1,64}$")


def is_staging(name: str) -> bool:
    return _STAGING.fullmatch(name) is not None


class CpuExecutionError(ValueError):
    """The execution inputs or retained evidence do not verify."""


class _NeedOperation(Exception):
    """Pure replay reached the first unclaimed operation."""


class _ExecutionBlocked(Exception):
    """A failed or uncertain terminal claim prevents later operations."""


def entries(path: Path, maximum: int) -> set[str]:
    _safe(path.absolute())
    names: set[str] = set()
    staging_count = 0
    with os.scandir(path) as iterator:
        for entry in iterator:
            _safe(Path(entry.path).absolute())
            if is_staging(entry.name):
                staging_count += 1
                if staging_count > 8:
                    raise CpuExecutionError("execution staging inventory exceeds its bounded allowance")
                file_identity(Path(entry.path), max_bytes=4 * 1024 * 1024)
                continue
            if len(names) == maximum:
                raise CpuExecutionError("execution inventory exceeds its declared bound")
            names.add(entry.name)
    return names


def prepare(root: Path, manifest: dict[str, Any], *, write: bool, operation_count: int,
            search: bool) -> None:
    allowed = {".cpu-execution.lock", "manifest.json", "projection", "report.json", *DIRECTORIES}
    if search:
        allowed.add("materializations")
    names = entries(root, len(allowed))
    if names - allowed:
        raise CpuExecutionError("execution directory contains unowned entries")
    for name in names - {*DIRECTORIES, "projection", "materializations"}:
        with regular_file(root / name):
            pass
    if "manifest.json" not in names:
        if names - {".cpu-execution.lock"}:
            raise CpuExecutionError("execution evidence exists without its manifest")
        if not write:
            raise CpuExecutionError("execution manifest is missing")
        _immutable_json(root / "manifest.json", manifest)
    else:
        _same(_read_record(root / "manifest.json"), manifest, "execution manifest or runtime profile changed")
    for name in (*DIRECTORIES, "projection"):
        path = root / name
        if write:
            path.mkdir(exist_ok=True)
        if not path.is_dir():
            raise CpuExecutionError("execution journal directory is missing")
    lists = {name: entries(root / name, operation_count) for name in DIRECTORIES}
    for directory, names in lists.items():
        if names != {f"{i:08d}.json" for i in range(len(names))}:
            raise CpuExecutionError(f"execution {directory} is not a contiguous bounded prefix")
        for name in names:
            with regular_file(root / directory / name):
                pass
    if (not lists["results"] <= lists["outcomes"] <= lists["claims"]
            or not lists["checks"] <= lists["outcomes"]
            or len(lists["claims"]) - len(lists["results"]) > 1):
        raise CpuExecutionError("execution publication stages differ from a single terminal operation")
    # Failed boundary outcomes do not require a callback check. Completed ones
    # do; check names may therefore stop one position before the terminal result.
    if len(lists["checks"]) < len(lists["results"]) - 1:
        raise CpuExecutionError("execution results are missing callback validation evidence")


class ExecutionJournal:
    """Structural adapter consumed by the unchanged search/reference workflows."""

    def __init__(self, spec: Any, root: Path, contract: CpuExecutionContract,
                 registry: TensorCandidateRegistry, *, replay: bool, reference: Any = None) -> None:
        self.spec, self.reference, self.root = spec, reference, root / "projection"
        self.evidence_root, self.contract, self.registry, self.replay = root, contract, registry, replay
        self.runtime_digest = contract.native_runtime_digest
        self.available = len(entries(root / "claims", 256))
        self.index = 0
        self.previous = "0" * 64
        self.spent = {meter: 0 for meter in METERS}
        self.seen_artifacts: set[str] = set()
        self.was_complete = (self.root / "complete.json").exists()
        self.receipts: dict[str, CpuBatchReceipt] = {}
        self.materialized: set[str] = set()
        self.status = "uncertain"
        self.terminal_claim: dict[str, Any] | None = None
        self.terminal_result: dict[str, Any] | None = None
        self.terminal_outcome: dict[str, Any] | None = None
        self.reconciliation_needed = False
        self.observation_inventory: list[dict[str, Any]] = []

    def _current(self) -> None:
        identity = execution_runtime_identity()
        for name in ("implementation_digest", "native_runtime_digest", "measured_profile_digest"):
            if identity[name] != getattr(self.contract, name):
                raise CpuExecutionError("execution source/runtime pins changed before an operation or validation")

    def _observation(self, claim: dict[str, Any], outcome: Any = None) -> dict[str, Any]:
        label, kind = claim["label"], claim["kind"]
        phase = ("reference" if self.reference is not None else "restoration" if label.endswith("-restored")
                 else "confirmation" if label.startswith("confirmation-") else "audit" if label.startswith("audit-")
                 else "practice" if kind == "inference" else "noninference")
        measured = outcome.measured if outcome is not None else None
        return {"index": claim["index"], "kind": kind, "label": label, "phase": phase,
                "outcome_digest": outcome.digest if outcome is not None else None,
                "outcome_status": outcome.status if outcome is not None else None,
                "check_status": None, "result_status": None,
                "outcome_present": outcome is not None, "measured": measured is not None if outcome is not None else None,
                "process_observation": measured.process.to_dict() if measured is not None else None}

    def perform(self, kind: str, label: str, inputs: dict[str, Any], reserved: dict[str, int],
                execute: Callable[[], dict[str, Any]], validate: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
        if set(reserved) != set(METERS) or any(type(v) is not int or v < 0 for v in reserved.values()):
            raise CpuExecutionError("invalid execution reservation")
        spent = {name: self.spent[name] + reserved[name] for name in METERS}
        if any(value > self.contract.capacity.to_dict()[name] for name, value in spent.items()):
            raise CpuExecutionError("execution exhausted its pinned capacity")
        claim = {"schema_version": 1, "contract_digest": self.contract.digest, "index": self.index,
                 "kind": kind, "label": label, "input": inputs, "reserved": reserved,
                 "previous_result_digest": self.previous}
        requests = operation_requests(self.contract, claim)
        name = f"{self.index:08d}.json"
        paths = {directory: self.evidence_root / directory / name for directory in DIRECTORIES}
        retained = self.index < self.available
        if retained:
            _same(_read_record(paths["claims"]), claim, "execution claim differs from frozen workflow replay")
        else:
            if self.replay:
                raise _NeedOperation
            if self.was_complete:
                raise CpuExecutionError("completed execution is missing an operation")
            self._current()
            _immutable_json(paths["claims"], claim)
            self.available += 1
        self.spent, self.terminal_claim = spent, claim
        self.terminal_outcome = self.terminal_result = None
        if paths["outcomes"].exists():
            outcome = validate_cpu_operation_outcome(_read_record(paths["outcomes"]), self.contract, claim)
        elif retained:
            self.status = "uncertain"
            self.observation_inventory.append(self._observation(claim))
            raise _ExecutionBlocked("claimed operation has no returned outcome; redispatch is forbidden")
        else:
            if kind == "inference":
                candidate = next((item for item in self.registry.candidates if item.digest == inputs["candidate_digest"]), None)
                if candidate is None:
                    raise CpuExecutionError("inference candidate is not in the pinned registry")
                # Do not invoke the native execute closure: its runner is frozen.
                # An exception before a measured outcome leaves this claim uncertain.
                try:
                    self.registry.verify(candidate.handle)
                    measured = run_measured_cpu_batch(requests, checkpoint_path=self.registry.checkpoint_path(candidate.handle),
                                                      contract=self.contract.failure_contract)
                except Exception:
                    self.status = "uncertain"
                    self.observation_inventory.append(self._observation(claim))
                    raise _ExecutionBlocked("inference returned no measured outcome; retained claim remains uncertain") from None
                outcome = build_cpu_operation_outcome(self.contract, claim, measured=measured)
            else:
                try:
                    value = execute()
                except Exception as exc:
                    outcome = build_cpu_operation_outcome(self.contract, claim, exception=exc)
                else:
                    outcome = build_cpu_operation_outcome(self.contract, claim, value=value)
            # Outcome publication precedes all adapter callback validation.
            _immutable_json(paths["outcomes"], outcome.to_dict())
            outcome = validate_cpu_operation_outcome(outcome, self.contract, claim)
        self.terminal_outcome = outcome.to_dict()
        self.observation_inventory.append(self._observation(claim, outcome))
        if outcome.status == "completed":
            value = operation_value(outcome)
            if paths["checks"].exists():
                check = validate_cpu_operation_check(_read_record(paths["checks"]), self.contract, claim, outcome)
                if check["status"] == "accepted":
                    self._current()
                    validate(value)
            else:
                try:
                    self._current()
                    validate(value)
                except Exception as exc:
                    if retained:
                        raise CpuExecutionError("saved outcome no longer passes current callback validation") from exc
                    check = build_cpu_operation_check(self.contract, claim, outcome, exception=exc)
                else:
                    check = build_cpu_operation_check(self.contract, claim, outcome)
                if self.replay:
                    self.reconciliation_needed = True
                else:
                    _immutable_json(paths["checks"], check)
            result = {"schema_version": 1, "contract_digest": self.contract.digest,
                      "operation_digest": canonical_digest(claim), "outcome_digest": outcome.digest,
                      "check_digest": canonical_digest(check),
                      "status": "completed" if check["status"] == "accepted" else "failed"}
        else:
            if paths["checks"].exists():
                raise CpuExecutionError("failed boundary outcome cannot contain an accepted callback check")
            value = {}
            result = {"schema_version": 1, "contract_digest": self.contract.digest,
                      "operation_digest": canonical_digest(claim), "outcome_digest": outcome.digest,
                      "check_digest": None, "status": "failed"}
        if paths["results"].exists():
            _same(_read_record(paths["results"]), result, "execution result differs from its durable outcome/check")
        elif self.replay:
            self.reconciliation_needed = True
        else:
            _immutable_json(paths["results"], result)
        self.terminal_result = result if paths["results"].exists() else None
        self.observation_inventory[-1]["check_status"] = (
            check["status"] if outcome.status == "completed" and paths["checks"].exists() else None
        )
        self.observation_inventory[-1]["result_status"] = result["status"] if paths["results"].exists() else None
        if result["status"] == "failed":
            self.status = "failed"
            raise _ExecutionBlocked("failed operation remains charged; redispatch is forbidden")
        self.previous = canonical_digest(result)
        self.index += 1
        return value

    def publish(self, name: str, value: dict[str, Any]) -> None:
        self.seen_artifacts.add(name)
        path = self.root / name
        if path.exists():
            _same(_read_record(path), value, "native semantic projection differs from measured journal replay")
        elif self.was_complete or self.index < self.available:
            raise CpuExecutionError("execution is missing an earlier native projection artifact")
        elif self.replay:
            self.reconciliation_needed = True
        else:
            _immutable_json(path, value)
