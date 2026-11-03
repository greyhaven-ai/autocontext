"""Durable, bounded editing sessions for trusted CPU-only fixture adapters.

Every dispatch first consumes an immutable reservation. Replaying the entire
journal verifies lineage, requests, scoring, counters and the source closure;
a published result can finish without another dispatch, while an unresolved
claim blocks progress. This is local integrity checking, not remote attestation
or a sandbox for arbitrary Python adapters.

Byte meters count canonical UTF-8 JSON at the editor and probe boundaries (the
two stages intentionally overlap). Time is fixture-simulated, never a measured
wall-time limit or a claim about model tokens, GPUs or actual monetary cost.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from pydantic import TypeAdapter

from autocontext.util.file_lock import advisory_path_lock
from autocontext.weight_evolution.artifacts import canonical_digest, file_identity, load_small_json, regular_file
from autocontext.weight_evolution.comparison_fixtures import SyntheticComparisonScorer
from autocontext.weight_evolution.comparison_models import (
    ArtifactSource,
    CheckpointArtifact,
    ContextArtifact,
    DecodingArtifact,
)
from autocontext.weight_evolution.session_data import SessionDataBundle
from autocontext.weight_evolution.session_models import (
    AdapterResult,
    CandidateRevision,
    EditOperation,
    InspectOperation,
    PracticeFeedback,
    ProbeOperation,
    SessionAdapterRequest,
    SessionOperation,
    SessionSpec,
    SessionUsage,
    apply_edit,
    initial_candidate,
)

MAX_JOURNAL_RECORD_BYTES = 16 * 1024 * 1024
_OPERATION = TypeAdapter(SessionOperation)
_DIRECTORIES = ("claims", "results", "completed", "failures")
_NAME = re.compile(r"[0-9]{8}\.json\Z")
_STAGING_NAME = re.compile(r"\.session-publish-[a-zA-Z0-9_-]{8,}\Z")
MAX_EXPORTED_SEARCH_UNITS = 1000000000
SessionArtifact = ContextArtifact | CheckpointArtifact | DecodingArtifact


class SessionError(ValueError):
    """A session request cannot proceed under its pinned contract."""


class SessionIntegrityError(SessionError):
    """Stored session evidence differs from its verified history."""


class SessionBlockedError(SessionError):
    """An ambiguous dispatch cannot be retried or skipped automatically."""


class SessionAdapter(Protocol):
    @property
    def identity_digest(self) -> str: ...

    @property
    def execution_kind(self) -> str: ...

    def execute(self, request: SessionAdapterRequest) -> AdapterResult: ...


def _encode(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")


def _same(actual: object, expected: object, message: str) -> None:
    if _encode(actual) != _encode(expected):
        raise SessionIntegrityError(message)


def _record(payload: dict[str, Any]) -> dict[str, Any]:
    return {"payload": payload, "digest": canonical_digest(payload)}


def _read_record(path: Path) -> dict[str, Any]:
    raw = load_small_json(path, max_bytes=MAX_JOURNAL_RECORD_BYTES)
    if not isinstance(raw, dict) or set(raw) != {"payload", "digest"} or not isinstance(raw["payload"], dict):
        raise SessionIntegrityError(f"invalid journal record: {path.name}")
    if canonical_digest(raw["payload"]) != raw["digest"]:
        raise SessionIntegrityError(f"journal digest mismatch: {path.name}")
    return raw["payload"]


def _immutable_json(path: Path, payload: dict[str, Any]) -> None:
    """Publish a complete fsynced record without replacing existing evidence."""
    encoded = _encode(_record(payload))
    if len(encoded) > MAX_JOURNAL_RECORD_BYTES:
        raise SessionError("session journal record exceeds the bounded JSON size")
    if path.exists() or path.is_symlink():
        _same(_read_record(path), payload, "immutable journal record already contains different evidence")
        return
    descriptor, temporary = tempfile.mkstemp(prefix=".session-publish-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        # link is atomic and refuses to overwrite a competing publication.
        os.link(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        os.unlink(temporary)


def _clear_staging(directory: Path) -> None:
    """Discard unpublished scratch links after a crash, never journal evidence.

    A crash can occur after the immutable link is durable but before unlinking
    its temporary name. Results remain authoritative; absence of a result still
    leaves its claim ambiguous. Call only while holding the session lock.
    """
    staged = tuple(path for path in directory.iterdir() if _STAGING_NAME.fullmatch(path.name))
    if len(staged) > 16:
        raise SessionIntegrityError("too many interrupted journal staging files")
    for path in staged:
        with regular_file(path) as stream:
            if os.fstat(stream.fileno()).st_size > MAX_JOURNAL_RECORD_BYTES:
                raise SessionIntegrityError("journal staging file exceeds its bounded size")
        path.unlink()
    if staged:
        descriptor = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def _search_units(usage: SessionUsage) -> int:
    return sum(getattr(usage, name) for name in (
        "editor_input_bytes", "editor_output_bytes", "probe_input_bytes", "probe_output_bytes"))


def controller_provenance() -> dict[str, Any]:
    root = Path(__file__).parent
    names = ("session.py", "session_models.py", "session_data.py", "comparison_models.py",
             "comparison_fixtures.py", "scorers.py", "artifacts.py", "models.py", "datasets.py")
    return {"name": "bounded-fixture-editing-session-v1", "execution_kind": "fixture",
            "model_execution_performed": False, "gpu_execution_deferred": True,
            "meter": "canonical-utf8-json-bytes-and-simulated-seconds",
            "reservation_policy": "all-claimed-reservations-consumed-without-refunds",
            "source_files": [file_identity(root / name, max_bytes=1024 * 1024) for name in names]}


@dataclass
class _History:
    candidate: CandidateRevision
    tip: str
    reserved: SessionUsage = field(default_factory=SessionUsage)
    observed: SessionUsage = field(default_factory=SessionUsage)
    feedback: list[PracticeFeedback] = field(default_factory=list)
    receipts: list[dict[str, Any]] = field(default_factory=list)
    operations: list[SessionOperation] = field(default_factory=list)
    practice_ids: set[str] = field(default_factory=set)
    probed_candidates: set[str] = field(default_factory=set)
    blocked: bool = False
    sealed: bool = False


class EditingSession:
    """Stateful inspect/edit/probe controller; only practice projections escape."""

    def __init__(self, *, spec: SessionSpec, data: SessionDataBundle, output_dir: Path,
                 adapter: SessionAdapter) -> None:
        self.spec = SessionSpec.model_validate_json(_encode(spec.to_dict()))
        self.data = SessionDataBundle.model_validate_json(_encode(data.to_dict()))
        self.adapter = adapter
        self.output_dir = Path(output_dir).absolute()
        self._scorer = SyntheticComparisonScorer()
        self._check_identity()
        self._safe_directory(self.output_dir, create=True)
        self._lock_path = self.output_dir / ".session.lock"
        self._check_lock()
        with advisory_path_lock(self._lock_path):
            manifest = self.output_dir / "manifest.json"
            if not manifest.exists() and not manifest.is_symlink():
                if {path.name for path in self.output_dir.iterdir()} - {".session.lock"}:
                    raise SessionIntegrityError("refusing to adopt a nonempty directory without a session manifest")
                for name in _DIRECTORIES:
                    self._safe_directory(self.output_dir / name, create=True)
                _immutable_json(manifest, self._manifest())
            self._replay()

    @staticmethod
    def _safe_directory(path: Path, *, create: bool = False) -> None:
        if any(parent.is_symlink() for parent in (path, *path.parents)):
            raise SessionIntegrityError("symlink session paths are forbidden")
        if create:
            path.mkdir(parents=True, exist_ok=True)
        if not path.is_dir():
            raise SessionIntegrityError("session journal directory is missing or not a directory")

    def _check_lock(self) -> None:
        self._safe_directory(self.output_dir)
        if self._lock_path.is_symlink():
            raise SessionIntegrityError("symlink session lock is forbidden")
        if self._lock_path.exists():
            with regular_file(self._lock_path):
                pass

    def _check_identity(self) -> None:
        if self.adapter.execution_kind != "fixture" or self.adapter.identity_digest != self.spec.adapter_digest:
            raise SessionIntegrityError("fixture adapter differs from the pinned session identity")
        if self.data.digest != self.spec.data_digest or self._scorer.lock_digest != self.spec.scorer_digest:
            raise SessionIntegrityError("session data or scorer differs from the pinned identity")
        for case in self.data.practice:
            self._scorer.validate_cohort(case.dataset, (case.problem,))

    def _manifest(self) -> dict[str, Any]:
        self._check_identity()
        return {"schema_version": 1, "spec": self.spec.to_dict(), "spec_digest": self.spec.digest,
                "split_commitments": self.data.split_commitments(), "controller": controller_provenance()}

    def _inventory(self) -> dict[str, set[int]]:
        self._safe_directory(self.output_dir)
        _clear_staging(self.output_dir)
        allowed = {*_DIRECTORIES, ".session.lock", "manifest.json", "export.json"}
        if {path.name for path in self.output_dir.iterdir()} - allowed:
            raise SessionIntegrityError("unexpected files in the session journal")
        inventory: dict[str, set[int]] = {}
        for name in _DIRECTORIES:
            directory = self.output_dir / name
            self._safe_directory(directory)
            _clear_staging(directory)
            paths = tuple(directory.iterdir())
            if len(paths) > self.spec.limits.max_operations:
                raise SessionIntegrityError("journal exceeds its operation budget")
            if any(not _NAME.fullmatch(path.name) or path.is_symlink() or not path.is_file() for path in paths):
                raise SessionIntegrityError("invalid session journal entry path")
            inventory[name] = {int(path.stem) for path in paths}
        claims = inventory["claims"]
        if claims != set(range(1, len(claims) + 1)):
            raise SessionIntegrityError("session claims are not a contiguous sequence")
        if (not inventory["results"] <= claims or not inventory["failures"] <= claims
                or not inventory["completed"] <= inventory["results"]
                or inventory["results"] & inventory["failures"]):
            raise SessionIntegrityError("orphaned or conflicting session outcomes")
        return inventory

    def _path(self, directory: str, sequence: int) -> Path:
        return self.output_dir / directory / f"{sequence:08d}.json"

    def _request(self, operation: SessionOperation, history: _History) -> SessionAdapterRequest:
        if operation.candidate_digest != history.candidate.digest:
            raise SessionError("operation references a stale candidate")
        if isinstance(operation, EditOperation):
            apply_edit(self.spec, history.candidate, operation)
        ids = operation.problem_ids if isinstance(operation, (InspectOperation, ProbeOperation)) else ()
        practice = self.data.project_practice(ids) if ids else ()
        return SessionAdapterRequest(spec_digest=self.spec.digest, operation=operation, candidate=history.candidate,
                                     practice=practice, feedback=tuple(history.feedback))

    @staticmethod
    def _input_usage(request: SessionAdapterRequest) -> SessionUsage:
        probe_bytes = 0
        if isinstance(request.operation, ProbeOperation):
            probe_bytes = len(_encode({"candidate": request.candidate.state.to_dict(),
                                       "practice": [row.to_dict() for row in request.practice]}))
        return SessionUsage(operations=1, editor_input_bytes=len(_encode(request.to_dict())),
                            probe_input_bytes=probe_bytes)

    def _claim(self, operation: SessionOperation, request: SessionAdapterRequest, history: _History) -> dict[str, Any]:
        if not self._input_usage(request).within(operation.reservation):
            raise SessionError("request input exceeds its reserved byte budget")
        total = history.reserved.add(operation.reservation)
        if not total.within_limits(self.spec.limits):
            raise SessionError("operation exceeds the cumulative session budget")
        if _search_units(total) > MAX_EXPORTED_SEARCH_UNITS:
            raise SessionError("operation exceeds the aggregate reserved-byte budget supported by M5 exports")
        claim = {"schema_version": 1, "sequence": len(history.operations) + 1, "spec_digest": self.spec.digest,
                 "previous_digest": history.tip, "operation": operation.to_dict(), "operation_digest": operation.digest,
                 "request": request.to_dict(), "request_digest": request.digest,
                 "reserved_total": total.to_dict()}
        if len(_encode(_record(claim))) > MAX_JOURNAL_RECORD_BYTES:
            raise SessionError("request exceeds the bounded journal record size")
        return claim

    def _complete(self, claim: dict[str, Any], request: SessionAdapterRequest, result: AdapterResult,
                  history: _History) -> dict[str, Any]:
        operation = request.operation
        if result.operation_digest != operation.digest:
            raise SessionError("adapter result references another operation")
        expected = operation.problem_ids if isinstance(operation, ProbeOperation) else ()
        if tuple(row.problem_id for row in result.probe_outputs) != expected:
            raise SessionError("probe outputs differ from the selected practice problems")
        inputs = self._input_usage(request)
        measured = SessionUsage(operations=1, editor_input_bytes=inputs.editor_input_bytes,
                                editor_output_bytes=len(_encode(result.to_dict())),
                                probe_input_bytes=inputs.probe_input_bytes,
                                probe_output_bytes=len(_encode([row.to_dict() for row in result.probe_outputs]))
                                if isinstance(operation, ProbeOperation) else 0,
                                simulated_seconds=result.simulated_seconds)
        if not measured.within(operation.reservation):
            raise SessionError("adapter result exceeds the reserved byte or simulated-time budget")
        if len(_encode(_record(result.to_dict()))) > MAX_JOURNAL_RECORD_BYTES:
            raise SessionError("adapter result exceeds the bounded journal record size")
        candidate = apply_edit(self.spec, history.candidate, operation) if isinstance(operation, EditOperation) \
            else history.candidate
        cases = {case.problem.problem_id: case for case in self.data.practice}
        feedback = [PracticeFeedback(problem_id=row.problem_id,
                                     score=self._scorer.score_sample(cases[row.problem_id].dataset,
                                                                     cases[row.problem_id].problem, row),
                                     response_sha256=hashlib.sha256(row.text.encode("utf-8")).hexdigest(),
                                     candidate_digest=candidate.digest).to_dict() for row in result.probe_outputs]
        return {"schema_version": 1, "status": "completed", "claim_digest": canonical_digest(claim),
                "request_digest": request.digest, "operation_digest": operation.digest,
                "result_digest": result.digest, "candidate": candidate.to_dict(), "candidate_digest": candidate.digest,
                "feedback": feedback, "charged_usage": operation.reservation.to_dict(),
                "measured_usage": measured.to_dict(), "reserved_total": claim["reserved_total"],
                "observed_total": history.observed.add(measured).to_dict()}

    @staticmethod
    def _failure(claim: dict[str, Any], request: SessionAdapterRequest, history: _History,
                 error: dict[str, Any]) -> dict[str, Any]:
        if (set(error) != {"type", "message"} or type(error["type"]) is not str or not 1 <= len(error["type"]) <= 128
                or type(error["message"]) is not str or len(error["message"]) > 2048):
            raise SessionIntegrityError("invalid terminal failure details")
        return {"schema_version": 1, "status": "failed", "claim_digest": canonical_digest(claim),
                "request_digest": request.digest, "operation_digest": request.operation.digest,
                "candidate": history.candidate.to_dict(), "candidate_digest": history.candidate.digest,
                "feedback": [], "error": error, "actual_usage_unknown": True,
                "charged_usage": request.operation.reservation.to_dict(), "measured_usage": None,
                "reserved_total": claim["reserved_total"], "observed_total": history.observed.to_dict()}

    def _advance(self, history: _History, operation: SessionOperation, request: SessionAdapterRequest,
                 receipt: dict[str, Any]) -> None:
        history.reserved = history.reserved.add(operation.reservation)
        history.operations.append(operation)
        history.practice_ids.update(row.problem_id for row in request.practice)
        history.receipts.append(receipt)
        history.tip = canonical_digest(receipt)
        if receipt["status"] == "completed":
            history.candidate = CandidateRevision.model_validate_json(_encode(receipt["candidate"]))
            history.observed = SessionUsage.model_validate_json(_encode(receipt["observed_total"]))
            history.feedback.extend(PracticeFeedback.model_validate_json(_encode(row)) for row in receipt["feedback"])
            if isinstance(operation, ProbeOperation):
                history.probed_candidates.add(history.candidate.digest)

    def _replay(self) -> _History:
        expected_manifest = self._manifest()
        _same(_read_record(self.output_dir / "manifest.json"), expected_manifest,
              "session manifest or controller source identity changed")
        inventory = self._inventory()
        history = _History(candidate=initial_candidate(self.spec), tip=canonical_digest(expected_manifest))
        ids: set[str] = set()
        for sequence in sorted(inventory["claims"]):
            if history.blocked:
                raise SessionIntegrityError("journal continued after an unresolved claim")
            claim = _read_record(self._path("claims", sequence))
            operation = _OPERATION.validate_json(_encode(claim.get("operation")))
            if operation.operation_id in ids:
                raise SessionIntegrityError("duplicate session operation identity")
            ids.add(operation.operation_id)
            request = self._request(operation, history)
            _same(claim, self._claim(operation, request, history), "claim differs from its verified parent/request/budget")
            if sequence in inventory["results"]:
                result = AdapterResult.model_validate_json(_encode(_read_record(self._path("results", sequence))))
                receipt = self._complete(claim, request, result, history)
                if sequence in inventory["completed"]:
                    _same(_read_record(self._path("completed", sequence)), receipt,
                          "completion differs from its verified result, lineage, feedback or counters")
                else:
                    _immutable_json(self._path("completed", sequence), receipt)
            elif sequence in inventory["failures"]:
                saved = _read_record(self._path("failures", sequence))
                receipt = self._failure(claim, request, history, saved.get("error", {}))
                _same(saved, receipt, "failure differs from its verified claim or budget")
            else:
                history.blocked = True
                receipt = {"schema_version": 1, "status": "blocked", "operation_digest": operation.digest,
                           "request_digest": request.digest, "claim_digest": canonical_digest(claim),
                           "candidate_digest": history.candidate.digest, "charged_usage": operation.reservation.to_dict(),
                           "reserved_total": claim["reserved_total"], "actual_usage_unknown": True}
            self._advance(history, operation, request, receipt)
        seal = self.output_dir / "export.json"
        if seal.exists() or seal.is_symlink():
            _same(_read_record(seal), self._export_payload(history), "sealed export differs from verified history")
            history.sealed = True
        return history

    def run(self, operation: SessionOperation) -> dict[str, Any]:
        """Return a terminal receipt; known failures consume reservations, not scores."""
        operation = _OPERATION.validate_json(_encode(operation.to_dict()))
        self._check_lock()
        with advisory_path_lock(self._lock_path):
            history = self._replay()
            for previous, receipt in zip(history.operations, history.receipts, strict=True):
                if previous.operation_id == operation.operation_id:
                    if previous.digest != operation.digest:
                        raise SessionError("operation ID was already claimed with a different payload")
                    if receipt["status"] == "blocked":
                        raise SessionBlockedError("unresolved operation cannot be dispatched again")
                    return receipt
            if history.blocked:
                raise SessionBlockedError("unresolved operation blocks subsequent dispatches")
            if history.sealed:
                raise SessionError("session is sealed; no new operations are allowed")
            request = self._request(operation, history)
            claim = self._claim(operation, request, history)
            sequence = claim["sequence"]
            _immutable_json(self._path("claims", sequence), claim)
            try:
                raw_result = self.adapter.execute(request)
                result = AdapterResult.model_validate_json(_encode(raw_result.to_dict()))
                receipt = self._complete(claim, request, result, history)
            except Exception as exc:
                receipt = self._failure(claim, request, history,
                                        {"type": type(exc).__name__[:128], "message": str(exc)[:2048]})
                _immutable_json(self._path("failures", sequence), receipt)
                return receipt
            # Publication failures leave the claim unresolved, or a valid result
            # which replay reconciles. Never label uncertain persistence a retry.
            _immutable_json(self._path("results", sequence), result.to_dict())
            _immutable_json(self._path("completed", sequence), receipt)
            return receipt

    def snapshot(self) -> dict[str, Any]:
        self._check_lock()
        with advisory_path_lock(self._lock_path):
            history = self._replay()
            return {"schema_version": 1, "execution_kind": "fixture", "model_execution_performed": False,
                    "gpu_execution_deferred": True,
                    "status": "sealed" if history.sealed else "blocked" if history.blocked else "active",
                    "candidate": history.candidate.to_dict(), "candidate_digest": history.candidate.digest,
                    "reserved_usage": history.reserved.to_dict(), "observed_usage": history.observed.to_dict(),
                    "operations": history.receipts, "operation_digests": [op.digest for op in history.operations],
                    "source_problem_ids": sorted(history.practice_ids), "history_digest": history.tip}

    def _artifact(self, history: _History) -> SessionArtifact:
        source = ArtifactSource(kind="development_fixture", source_problem_ids=tuple(sorted(history.practice_ids)),
                                declared_search_calls=history.reserved.operations,
                                declared_search_units=_search_units(history.reserved))
        identity = f"{self.spec.session_id}-{self.spec.mechanism.lower()}-candidate"
        state = history.candidate.state
        if self.spec.mechanism == "C":
            return ContextArtifact(artifact_id=identity, text=state.context_text, source=source)
        if self.spec.mechanism == "W":
            return CheckpointArtifact(artifact_id=identity, content=state.weight_descriptor, source=source)
        return DecodingArtifact(artifact_id=identity, source=source, **state.decoding.settings())

    def _export_payload(self, history: _History) -> dict[str, Any]:
        if history.blocked:
            raise SessionBlockedError("unresolved dispatch blocks candidate export")
        if history.candidate.state.digest == self.spec.baseline.state.digest:
            raise SessionError("export requires a candidate changed from the baseline")
        if history.candidate.digest not in history.probed_candidates:
            raise SessionError("export requires a completed practice probe of the current candidate")
        artifact = self._artifact(history)
        return {"schema_version": 1, "execution_kind": "fixture", "model_execution_performed": False,
                "gpu_execution_deferred": True, "status": "sealed", "spec_digest": self.spec.digest,
                "data_digest": self.data.digest, "history_digest": history.tip,
                "candidate_digest": history.candidate.digest, "artifact": artifact.to_dict(),
                "artifact_digest": artifact.digest, "reserved_usage": history.reserved.to_dict(),
                "observed_usage": history.observed.to_dict(),
                "cost_provenance": "verified-journal-reservations-including-failed-operations"}

    def export_candidate(self) -> SessionArtifact:
        """Seal and export current state with practice IDs/costs derived from history."""
        self._check_lock()
        with advisory_path_lock(self._lock_path):
            history = self._replay()
            payload = self._export_payload(history)
            _immutable_json(self.output_dir / "export.json", payload)
            return self._artifact(history)
