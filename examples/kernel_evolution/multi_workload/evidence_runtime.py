"""Deadline enforcement and durable raw evidence for the synthetic study."""

from __future__ import annotations

import json
import sys
import time
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from contract import EVIDENCE_ORIGIN, canonical_digest, digest, hardware_payload, protocol_payload
from contract_runtime import write_exact_bytes, write_exact_relative

from autocontext.kernel_evolution import (
    ExternalKernelBenchmarkRunner,
    KernelBenchmarkEvaluator,
    KernelBenchmarkEvaluatorConfig,
    KernelBenchmarkObservation,
    KernelBenchmarkProtocol,
    KernelCandidate,
)

_RETAINED_LAUNCHER = (
    "import os,runpy,sys; "
    "os.fchdir(int(sys.argv[1])); path=sys.argv[2]; sys.argv=sys.argv[2:]; "
    "sys.path.insert(0,os.path.dirname(path)); runpy.run_path(path,run_name='__main__')"
)


def _write_exact_text(path: Path, content: str) -> None:
    write_exact_bytes(path, content.encode("utf-8"))


def _write_exact_json(path: Path, payload: dict[str, Any]) -> None:
    encoded = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    _write_exact_text(path, encoded)


@dataclass(slots=True)
class WorkloadDeadline:
    """A pausable end-to-end wall budget charged only while its workload runs."""

    workload_id: str
    max_wall_seconds: float
    _consumed_seconds: float = 0.0
    _active_since: float | None = None
    _depth: int = 0

    @property
    def elapsed_seconds(self) -> float:
        active = time.monotonic() - self._active_since if self._active_since is not None else 0.0
        return self._consumed_seconds + active

    @property
    def remaining_seconds(self) -> float:
        return max(0.0, self.max_wall_seconds - self.elapsed_seconds)

    @contextmanager
    def active(self) -> Iterator[None]:
        if self._depth == 0:
            self._active_since = time.monotonic()
        self._depth += 1
        try:
            yield
        finally:
            self._depth -= 1
            if self._depth == 0:
                assert self._active_since is not None
                self._consumed_seconds += time.monotonic() - self._active_since
                self._active_since = None


@dataclass(slots=True)
class EvidenceRecorder:
    """Persist every raw observation/report and emit a manifest-bound index."""

    study_root: Path
    study_execution_id: str
    workload_specs_digest: str
    study_manifest_digest: str
    study_contract_digest: str
    study_backend_identity: str
    evidence_warning: str
    study_directory_fd: int | None = None
    records: list[dict[str, Any]] = field(default_factory=list)
    _sequence_by_stream: dict[str, int] = field(default_factory=dict)

    def _write(self, path: Path, content: str) -> None:
        if self.study_directory_fd is None:
            _write_exact_text(path, content)
            return
        try:
            relative = path.absolute().relative_to(self.study_root.absolute())
        except ValueError as exc:
            raise RuntimeError("evidence path escaped its retained study root") from exc
        write_exact_relative(self.study_directory_fd, relative, content.encode("utf-8"))

    def record(
        self,
        *,
        stream_id: str,
        observation: KernelBenchmarkObservation,
        evaluation_wall_seconds: float,
        planned_protocol_id: str,
        planned_plan_commitment: str,
        problem_contract_path: str,
        problem_contract_digest: str,
        evaluation_cost_usd: float = 0.0,
        execution_id: str,
        record_kind: str = "evaluation",
        derived_from_observation_digest: str | None = None,
        chargeable: bool = True,
    ) -> str:
        sequence = self._sequence_by_stream.get(stream_id, 0) + 1
        self._sequence_by_stream[stream_id] = sequence
        observation_json = observation.model_dump_json(indent=2)
        observation_digest = digest(observation_json)
        observation_path = self.study_root / "evidence" / "observations" / f"{observation_digest[7:]}.json"
        self._write(observation_path, observation_json)
        report_digest: str | None = None
        report_path: Path | None = None
        plan_commitment: str | None = None
        if observation.report is not None:
            report_json = observation.report.model_dump_json(indent=2)
            report_digest = digest(report_json)
            report_path = self.study_root / "evidence" / "reports" / f"{report_digest[7:]}.json"
            plan_commitment = observation.report.protocol.seed_commitment
            self._write(report_path, report_json)
        self.records.append(
            {
                "stream_id": stream_id,
                "sequence": sequence,
                "execution_id": execution_id,
                "record_kind": record_kind,
                "derived_from_observation_digest": derived_from_observation_digest,
                "chargeable": chargeable,
                "observation_digest": observation_digest,
                "observation_path": observation_path.relative_to(self.study_root).as_posix(),
                "report_digest": report_digest,
                "report_path": report_path.relative_to(self.study_root).as_posix() if report_path is not None else None,
                "candidate_artifact_digest": observation.candidate_artifact_digest,
                "candidate_source_digest": observation.candidate_source_digest,
                "incumbent_artifact_digest": observation.incumbent_artifact_digest,
                "incumbent_source_digest": observation.incumbent_source_digest,
                "protocol_id": observation.protocol_id,
                "plan_commitment": plan_commitment,
                "planned_protocol_id": planned_protocol_id,
                "planned_plan_commitment": planned_plan_commitment,
                "problem_contract_path": problem_contract_path,
                "problem_contract_digest": problem_contract_digest,
                "eligible": observation.eligible,
                "rejection_reason": observation.rejection_reason,
                "evaluation_wall_seconds": evaluation_wall_seconds,
                "evaluation_cost_usd": evaluation_cost_usd,
            }
        )
        return observation_digest

    def write_index(self) -> str:
        payload = {
            "schema_version": "autocontext.synthetic-kernel-study-evidence-index/v1",
            "evidence_origin": EVIDENCE_ORIGIN,
            "study_execution_id": self.study_execution_id,
            "workload_specs_digest": self.workload_specs_digest,
            "study_manifest_digest": self.study_manifest_digest,
            "study_contract_digest": self.study_contract_digest,
            "study_backend_identity": self.study_backend_identity,
            "evidence_warning": self.evidence_warning,
            "records": self.records,
        }
        path = self.study_root / "evidence" / "index.json"
        encoded = json.dumps(payload, indent=2, sort_keys=True) + "\n"
        self._write(path, encoded)
        return digest(encoded)


def rejected_observation(
    *,
    evaluator: KernelBenchmarkEvaluator,
    candidate: KernelCandidate,
    incumbent: KernelCandidate,
    reason: str,
    feedback: str,
    diagnostic: KernelBenchmarkObservation | None = None,
) -> KernelBenchmarkObservation:
    """Produce candidate-bound fail-closed evidence without inventing a report."""
    return KernelBenchmarkObservation(
        artifact_identity_version=candidate.artifact_identity_version,
        candidate_artifact_digest=candidate.artifact_digest,
        incumbent_artifact_digest=incumbent.artifact_digest,
        candidate_source_digest=candidate.source_digest,
        incumbent_source_digest=incumbent.source_digest,
        eligible=False,
        rejection_reason=reason,
        feedback=feedback,
        statistics_policy=evaluator.config.statistics_policy,
        stdout=diagnostic.stdout if diagnostic is not None else "",
        stderr=diagnostic.stderr if diagnostic is not None else "",
        stdout_truncated=diagnostic.stdout_truncated if diagnostic is not None else False,
        stderr_truncated=diagnostic.stderr_truncated if diagnostic is not None else False,
    )


class StudyEvaluator(KernelBenchmarkEvaluator):
    """Evaluator that persists every observation and consumes one workload deadline."""

    def __init__(
        self,
        runner: ExternalKernelBenchmarkRunner,
        config: KernelBenchmarkEvaluatorConfig,
        *,
        deadline: WorkloadDeadline,
        recorder: EvidenceRecorder,
        stream_id: str,
        planned_protocol: KernelBenchmarkProtocol,
        expected_scope_id: str,
        expected_baseline_id: str,
        problem_contract_path: str,
        problem_contract_digest: str,
        single_use: bool,
    ) -> None:
        super().__init__(runner, config)
        self._study_runner = runner
        self._deadline = deadline
        self._recorder = recorder
        self._stream_id = stream_id
        self._planned_protocol = planned_protocol
        self._expected_scope_id = expected_scope_id
        self._expected_baseline_id = expected_baseline_id
        self._reserved_consumed = False
        self._single_use = single_use
        self._execution_count = 0
        self._problem_contract_path = problem_contract_path
        self._problem_contract_digest = problem_contract_digest

    def manifest(self) -> dict[str, Any]:
        payload = super().manifest()
        payload["study_evidence"] = {
            "evidence_origin": EVIDENCE_ORIGIN,
            "study_execution_id": self._recorder.study_execution_id,
            "workload_specs_digest": self._recorder.workload_specs_digest,
            "study_manifest_digest": self._recorder.study_manifest_digest,
            "study_contract_digest": self._recorder.study_contract_digest,
            "study_backend_identity": self._recorder.study_backend_identity,
            "evidence_warning": self._recorder.evidence_warning,
            "stream_id": self._stream_id,
            "planned_protocol_id": self._planned_protocol.protocol_id,
            "planned_plan_commitment": self._planned_protocol.seed_commitment,
            "expected_scope_id": self._expected_scope_id,
            "expected_baseline_id": self._expected_baseline_id,
            "problem_contract_path": self._problem_contract_path,
            "problem_contract_digest": self._problem_contract_digest,
            "workload_wall_limit_seconds": self._deadline.max_wall_seconds,
        }
        return payload

    def evaluate_reserved(
        self,
        candidate: KernelCandidate,
        incumbent: KernelCandidate,
    ) -> KernelBenchmarkObservation:
        """Consume this evaluator's immutable protocol exactly once for a protected look."""
        if not self._single_use:
            raise RuntimeError("campaign primary evaluators do not expose the protected-look API")
        if self._reserved_consumed:
            raise RuntimeError("protected study protocol and private plan were already consumed")
        self._reserved_consumed = True
        return self._evaluate_once(
            candidate,
            incumbent,
            expected_scope_id=self._expected_scope_id,
            expected_baseline_id=self._expected_baseline_id,
            expected_protocol_id=self._planned_protocol.protocol_id,
        )

    def evaluate(
        self,
        candidate: KernelCandidate,
        incumbent: KernelCandidate,
        *,
        expected_scope_id: str | None = None,
        expected_baseline_id: str | None = None,
        expected_protocol_id: str | None = None,
    ) -> KernelBenchmarkObservation:
        if self._single_use:
            raise RuntimeError("protected study evaluators must be consumed through evaluate_reserved")
        return self._evaluate_once(
            candidate,
            incumbent,
            expected_scope_id=expected_scope_id,
            expected_baseline_id=expected_baseline_id,
            expected_protocol_id=expected_protocol_id,
        )

    def _evaluate_once(
        self,
        candidate: KernelCandidate,
        incumbent: KernelCandidate,
        *,
        expected_scope_id: str | None = None,
        expected_baseline_id: str | None = None,
        expected_protocol_id: str | None = None,
    ) -> KernelBenchmarkObservation:
        self._execution_count += 1
        execution_id = canonical_digest(
            {
                "kind": "autocontext.synthetic-kernel-evaluation/v1",
                "study_execution_id": self._recorder.study_execution_id,
                "workload_specs_digest": self._recorder.workload_specs_digest,
                "stream_id": self._stream_id,
                "execution_index": self._execution_count,
                "candidate_artifact_digest": candidate.artifact_digest,
                "incumbent_artifact_digest": incumbent.artifact_digest,
                "protocol_id": self._planned_protocol.protocol_id,
                "plan_commitment": self._planned_protocol.seed_commitment,
            }
        )
        started = time.monotonic()
        raw_observation: KernelBenchmarkObservation | None = None
        with self._deadline.active():
            remaining = self._deadline.remaining_seconds
            if remaining <= 0:
                observation = rejected_observation(
                    evaluator=self,
                    candidate=candidate,
                    incumbent=incumbent,
                    reason="study_wall_budget_exceeded",
                    feedback=f"Workload {self._deadline.workload_id!r} exhausted its wall-clock budget.",
                )
            else:
                effective = replace(self.config, timeout_seconds=min(float(self.config.timeout_seconds), remaining))
                raw_observation = KernelBenchmarkEvaluator(self._study_runner, effective).evaluate(
                    candidate,
                    incumbent,
                    expected_scope_id=expected_scope_id,
                    expected_baseline_id=expected_baseline_id,
                    expected_protocol_id=expected_protocol_id,
                )
                observation = raw_observation
                if self._deadline.remaining_seconds <= 0 and observation.eligible:
                    observation = rejected_observation(
                        evaluator=self,
                        candidate=candidate,
                        incumbent=incumbent,
                        reason="study_wall_budget_exceeded",
                        feedback=(
                            f"Workload {self._deadline.workload_id!r} exceeded its wall-clock budget "
                            "during evaluation; the completed measurement is retained for audit only."
                        ),
                        diagnostic=observation,
                    )
        elapsed = time.monotonic() - started
        if raw_observation is not None and raw_observation is not observation:
            raw_digest = self._recorder.record(
                stream_id=f"{self._stream_id}/over-budget-raw",
                observation=raw_observation,
                evaluation_wall_seconds=elapsed,
                planned_protocol_id=self._planned_protocol.protocol_id,
                planned_plan_commitment=self._planned_protocol.seed_commitment,
                problem_contract_path=self._problem_contract_path,
                problem_contract_digest=self._problem_contract_digest,
                evaluation_cost_usd=0.0,
                execution_id=execution_id,
                record_kind="raw-evaluation",
            )
        else:
            raw_digest = None
        self._recorder.record(
            stream_id=self._stream_id,
            observation=observation,
            evaluation_wall_seconds=0.0 if raw_digest is not None else elapsed,
            planned_protocol_id=self._planned_protocol.protocol_id,
            planned_plan_commitment=self._planned_protocol.seed_commitment,
            problem_contract_path=self._problem_contract_path,
            problem_contract_digest=self._problem_contract_digest,
            evaluation_cost_usd=0.0,
            execution_id=execution_id,
            record_kind="derived-rejection" if raw_digest is not None else "evaluation",
            derived_from_observation_digest=raw_digest,
            chargeable=raw_digest is None,
        )
        return observation


@dataclass(frozen=True, slots=True)
class EvaluationLook:
    observation: KernelBenchmarkObservation
    wall_seconds: float
    cost_usd: float = 0.0


def make_evaluator(
    problem: Path,
    *,
    problem_content: bytes,
    problem_id: str,
    deadline: WorkloadDeadline,
    recorder: EvidenceRecorder,
    stream_id: str,
    adapter: Path,
    immutable_paths: Sequence[Path],
    expected_immutable_files: Mapping[Path, bytes],
    single_use: bool,
) -> StudyEvaluator:
    problem_contract = json.loads(problem_content)
    if not isinstance(problem_contract, dict):
        raise ValueError("synthetic study problem contract must be a JSON object")
    directory_fd = recorder.study_directory_fd
    if directory_fd is None:
        raise RuntimeError("synthetic study execution requires a retained study directory")
    study_root = recorder.study_root.absolute()

    def retained(path: Path) -> str:
        return path.absolute().relative_to(study_root).as_posix()

    planned_protocol = KernelBenchmarkProtocol.model_validate(protocol_payload(problem_contract))
    runner = ExternalKernelBenchmarkRunner(
        [
            sys.executable,
            "-c",
            _RETAINED_LAUNCHER,
            str(directory_fd),
            retained(adapter),
            "--candidate",
            "{candidate}",
            "--incumbent",
            "{incumbent}",
            "--artifact-identity-version",
            "{artifact_identity_version}",
            "--candidate-artifact-digest",
            "{candidate_artifact_digest}",
            "--incumbent-artifact-digest",
            "{incumbent_artifact_digest}",
            "--candidate-source-digest",
            "{candidate_source_digest}",
            "--incumbent-source-digest",
            "{incumbent_source_digest}",
            "--candidate-source-suffix",
            "{candidate_source_suffix}",
            "--incumbent-source-suffix",
            "{incumbent_source_suffix}",
            "--candidate-entrypoint",
            "{candidate_entrypoint}",
            "--incumbent-entrypoint",
            "{incumbent_entrypoint}",
            "--report",
            "{report}",
            "--problem",
            retained(problem),
        ],
        trusted_unsafe=True,
        immutable_paths=immutable_paths,
        expected_immutable_files=expected_immutable_files,
        inherited_fds=(directory_fd,),
        environment={"PYTHONDONTWRITEBYTECODE": "1"},
    )
    return StudyEvaluator(
        runner,
        KernelBenchmarkEvaluatorConfig(
            problem_id=problem_id,
            timeout_seconds=min(630.0, deadline.max_wall_seconds),
            min_timing_blocks=10,
            bootstrap_samples=2_000,
        ),
        deadline=deadline,
        recorder=recorder,
        stream_id=stream_id,
        planned_protocol=planned_protocol,
        expected_scope_id=canonical_digest(hardware_payload(problem_contract)),
        expected_baseline_id=digest(problem_contract["reference_identity"]),
        problem_contract_path=problem.relative_to(recorder.study_root).as_posix(),
        problem_contract_digest=digest(problem_content),
        single_use=single_use,
    )


def evaluate_final(
    evaluator: StudyEvaluator,
    *,
    candidate: KernelCandidate,
    baseline: KernelCandidate,
) -> EvaluationLook:
    """Consume one protected reservation for one candidate-bound evaluation."""
    started = time.monotonic()
    observation = evaluator.evaluate_reserved(candidate, baseline)
    return EvaluationLook(observation=observation, wall_seconds=time.monotonic() - started)


__all__ = [
    "EvidenceRecorder",
    "EvaluationLook",
    "StudyEvaluator",
    "WorkloadDeadline",
    "evaluate_final",
    "make_evaluator",
    "rejected_observation",
]
