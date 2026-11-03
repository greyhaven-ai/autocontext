"""Durable CPU inference over fixed, verified tiny checkpoint candidates.

This evaluates constructed tasks, not Qwen or adaptive optimization. The worker
receives no gold labels. Old fixture contracts and their source pins stay frozen.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal, Self

from pydantic import Field, model_validator

from autocontext.util.file_lock import advisory_path_lock
from autocontext.weight_evolution.artifacts import canonical_digest, file_identity, load_small_json, regular_file
from autocontext.weight_evolution.checkpoint_candidates import TensorCandidateRegistry, TensorCandidateSpec
from autocontext.weight_evolution.cpu_model import verify_model_checkpoint
from autocontext.weight_evolution.cpu_models import CpuDecoding, CpuInferenceRequest, CpuModelContract, CpuWorkerLimits
from autocontext.weight_evolution.cpu_runner import CpuBatchReceipt, run_cpu_batch, runtime_identity, validate_cpu_receipt
from autocontext.weight_evolution.models import Digest, FrozenModel, Identifier
from autocontext.weight_evolution.session import _clear_staging, _immutable_json, _read_record, _same

CONDITIONS = ("A", "C", "W", "CW", "D")
ROLES = ("practice", "confirmation", "audit")
MAX_BUNDLE_BYTES = 1024 * 1024


class CpuCase(FrozenModel):
    case_id: Identifier
    role: Literal["practice", "confirmation", "audit"]
    prompt: str = Field(min_length=1, max_length=4096)
    expected: str = Field(min_length=1, max_length=128)


class CpuStudyBudget(FrozenModel):
    max_inference_calls: int = Field(default=128, gt=0, le=512)
    max_input_tokens: int = Field(default=1024, gt=0, le=32768)
    max_output_tokens: int = Field(default=128, gt=0, le=512)


class CpuStudySpec(FrozenModel):
    schema_version: Literal[1] = 1
    study_kind: Literal["fixed_candidate_cpu_inference"] = "fixed_candidate_cpu_inference"
    study_id: Identifier
    model: CpuModelContract
    candidates: tuple[TensorCandidateSpec, ...] = Field(min_length=3, max_length=3)
    helpful_candidate: Identifier
    harmful_candidate: Identifier
    selected_candidate: Identifier
    context: str = Field(min_length=1, max_length=1024)
    baseline_decoding: CpuDecoding
    alternative_decoding: CpuDecoding
    limits: CpuWorkerLimits
    budget: CpuStudyBudget
    cases: tuple[CpuCase, ...] = Field(min_length=6, max_length=48)
    minimum_confirmation_gain: float = Field(default=0.1, gt=0, le=1, allow_inf_nan=False)
    implementation_digest: Digest

    @model_validator(mode="after")
    def fixed_design(self) -> Self:
        if len({case.case_id for case in self.cases}) != len(self.cases):
            raise ValueError("case IDs must be globally unique")
        if len({tuple(case.prompt.split()) for case in self.cases}) != len(self.cases):
            raise ValueError("split prompts must be distinct after tokenization")
        for role in ROLES:
            if not 2 <= sum(case.role == role for case in self.cases) <= 16:
                raise ValueError("each split needs 2..16 cases")
        if any(case.expected not in self.model.output_tokens for case in self.cases):
            raise ValueError("case label is outside the model vocabulary")
        prompts = [self.context, *(case.prompt for case in self.cases)]
        if any(not text.split() or any(token not in self.model.vocabulary for token in text.split()) for text in prompts):
            raise ValueError("study prompts contain unknown tokens")
        if any(len((self.context + " " + case.prompt).split()) > self.limits.max_input_tokens for case in self.cases):
            raise ValueError("rendered study prompt exceeds the worker token bound")
        ids = {candidate.candidate_id for candidate in self.candidates}
        edited = {candidate.candidate_id for candidate in self.candidates if candidate.program is not None}
        if len(ids) != 3 or edited != {self.helpful_candidate, self.harmful_candidate} or len(edited) != 2:
            raise ValueError("study requires baseline and distinct helpful/harmful candidates")
        if self.selected_candidate not in edited:
            raise ValueError("selected checkpoint must be a fixed edited candidate")
        if self.baseline_decoding == self.alternative_decoding:
            raise ValueError("D must change decoding settings")
        return self

    @property
    def digest(self) -> str:
        return canonical_digest(self.to_dict())


class CpuBundleLock(FrozenModel):
    schema_version: Literal[1] = 1
    kind: Literal["cpu_inference_bundle"] = "cpu_inference_bundle"
    spec_sha256: Digest
    spec_size_bytes: int = Field(gt=0, le=MAX_BUNDLE_BYTES)
    base_sha256: Digest
    base_size_bytes: int = Field(gt=0, le=4 * 1024 * 1024)


def study_implementation_digest() -> str:
    package = Path(__file__).parent
    names = ("cpu_study.py", "cpu_model.py", "cpu_models.py", "cpu_runner.py", "cpu_worker.py",
             "tensor_edits.py", "checkpoint_candidates.py", "artifacts.py", "models.py", "session.py", "session_models.py")
    return canonical_digest({name: file_identity(package / name) for name in names})


def _safe(path: Path) -> None:
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise ValueError("CPU study paths must not traverse symlinks")


def load_cpu_study(directory: Path) -> CpuStudySpec:
    lock = CpuBundleLock.model_validate_json(json.dumps(load_small_json(directory / "bundle.lock.json")))
    for name, digest, size in (("study.json", lock.spec_sha256, lock.spec_size_bytes),
                               ("base.safetensors", lock.base_sha256, lock.base_size_bytes)):
        actual = file_identity(directory / name, max_bytes=size)
        if actual["sha256"] != digest or actual["size_bytes"] != size:
            raise ValueError("CPU bundle byte/hash pin mismatch")
    raw_spec = load_small_json(directory / "study.json", max_bytes=MAX_BUNDLE_BYTES)
    spec = CpuStudySpec.model_validate_json(json.dumps(raw_spec))
    _same(raw_spec, spec.to_dict(), "CPU study must preserve its exact typed representation")
    if spec.implementation_digest != study_implementation_digest():
        raise ValueError("CPU study implementation source changed")
    if any(candidate.contract.sha256 != lock.base_sha256 or candidate.contract.size_bytes != lock.base_size_bytes
           for candidate in spec.candidates):
        raise ValueError("CPU candidates differ from the pinned base")
    return spec


def _registry(spec: CpuStudySpec, directory: Path, output: Path) -> TensorCandidateRegistry:
    return TensorCandidateRegistry(spec.candidates, input_path=directory / "base.safetensors", output_dir=output)


def _candidate(spec: CpuStudySpec, name: str) -> TensorCandidateSpec:
    return next(candidate for candidate in spec.candidates if candidate.candidate_id == name)


def _requests(spec: CpuStudySpec, cases: tuple[CpuCase, ...], *, label: str,
              candidate: TensorCandidateSpec, context: str = "", decoding: CpuDecoding | None = None,
              ) -> tuple[CpuInferenceRequest, ...]:
    return tuple(CpuInferenceRequest(
        request_id=f"{label}.{case.case_id}", model=spec.model, checkpoint_sha256=candidate.output_sha256,
        checkpoint_size_bytes=candidate.output_size_bytes, prompt=" ".join(filter(None, (context, case.prompt))),
        decoding=decoding or spec.baseline_decoding, limits=spec.limits,
    ) for case in cases)


def _batches(spec: CpuStudySpec, baseline: TensorCandidateSpec) -> list[tuple[str, str, tuple[CpuInferenceRequest, ...]]]:
    batches = []
    practice = tuple(case for case in spec.cases if case.role == "practice")
    for label, name in (("control-base", baseline.candidate_id), ("control-helpful", spec.helpful_candidate),
                        ("control-harmful", spec.harmful_candidate), ("control-restored", baseline.candidate_id)):
        batches.append((label, name, _requests(spec, practice, label=label, candidate=_candidate(spec, name))))
    for role in ROLES:
        cases = tuple(case for case in spec.cases if case.role == role)
        for condition in CONDITIONS:
            name = spec.selected_candidate if condition in {"W", "CW"} else baseline.candidate_id
            label = f"{role}-{condition}"
            batches.append((label, name, _requests(
                spec, cases, label=label, candidate=_candidate(spec, name),
                context=spec.context if condition in {"C", "CW"} else "",
                decoding=spec.alternative_decoding if condition == "D" else spec.baseline_decoding,
            )))
    return batches


def _cost(requests: tuple[CpuInferenceRequest, ...]) -> dict[str, int]:
    return {"inference_calls": len(requests), "input_tokens": sum(len(r.prompt.split()) for r in requests),
            "output_tokens": len(requests)}


def _check_budget(cost: dict[str, int], budget: CpuStudyBudget) -> None:
    if any(cost[name] > getattr(budget, f"max_{name}") for name in cost):
        raise ValueError("CPU study exceeds its reserved inference budget")


def validate_cpu_study(directory: Path) -> dict[str, Any]:
    spec = load_cpu_study(directory)
    registry = _registry(spec, directory, directory / "unused-validation-store")
    registry.verify(registry.baseline.handle)
    verify_model_checkpoint(spec.model, directory / "base.safetensors",
                            expected_sha256=registry.baseline.output_sha256,
                            expected_size_bytes=registry.baseline.output_size_bytes)
    batches = _batches(spec, registry.baseline)
    cost = {name: sum(_cost(requests)[name] for _, _, requests in batches)
            for name in ("inference_calls", "input_tokens", "output_tokens")}
    _check_budget(cost, spec.budget)
    return {"study_digest": spec.digest, "planned_reservation": cost, "candidate_count": len(spec.candidates),
            "model_execution_performed": False, "gpu_execution_deferred": True,
            "scope": "validation-only-no-materialization-or-inference"}


def _score(cases: tuple[CpuCase, ...], receipt: CpuBatchReceipt) -> dict[str, Any]:
    if len(cases) != len(receipt.results):
        raise ValueError("CPU result count differs from scoring cases")
    rows: list[dict[str, Any]] = [
        {"case_id": case.case_id, "output": result.text, "correct": result.text == case.expected,
         "result_digest": result.digest} for case, result in zip(cases, receipt.results, strict=True)
    ]
    return {"accuracy": sum(row["correct"] for row in rows) / len(rows), "cases": rows}


def _read_receipt(path: Path, requests: tuple[CpuInferenceRequest, ...], runtime_digest: str) -> CpuBatchReceipt:
    raw = _read_record(path)
    receipt = CpuBatchReceipt.model_validate_json(json.dumps(raw))
    _same(raw, receipt.to_dict(), "CPU receipt JSON changed type during parsing")
    return validate_cpu_receipt(receipt, requests, runtime_digest)


def _run_locked(spec: CpuStudySpec, directory: Path, root: Path) -> dict[str, Any]:
    runtime = runtime_identity()
    runtime_digest = canonical_digest(runtime)
    manifest = {"schema_version": 1, "study": spec.to_dict(), "study_digest": spec.digest,
                "runtime": runtime, "runtime_digest": runtime_digest, "selection_policy": "fixed-before-inference"}
    entries = {path.name for path in root.iterdir()}
    allowed = {".cpu-study.lock", "manifest.json", "materializations", "seals", "claims", "results", "reports",
               "selection.json", "confirmation.json", "complete.json"}
    if entries - allowed or ("manifest.json" not in entries and entries - {".cpu-study.lock"}):
        raise ValueError("CPU study directory contains unowned evidence")
    for path in root.iterdir():
        _safe(path)
    was_complete = "complete.json" in entries
    _immutable_json(root / "manifest.json", manifest)
    registry = _registry(spec, directory, root / "materializations")
    registry.verify_existing()
    retained_claims = (root / "claims").exists() and any((root / "claims").iterdir())
    for name in ("claims", "results", "seals", "reports"):
        path = root / name
        if was_complete and not path.is_dir():
            raise ValueError("completed CPU study is missing retained evidence")
        path.mkdir(exist_ok=True)
        _clear_staging(path)
    batches = _batches(spec, registry.baseline)
    ordered = [f"{label}.json" for label, _, _ in batches]
    claimed = {path.name for path in (root / "claims").iterdir()}
    completed = {path.name for path in (root / "results").iterdir()}
    if claimed != set(ordered[:len(claimed)]) or completed != set(ordered[:len(completed)]) or not completed <= claimed:
        raise ValueError("retained CPU claims/results are not an ordered prefix")
    if len(claimed) != len(completed):
        raise ValueError("incomplete CPU claim remains charged; refusing a second dispatch")
    seal_names = {f"{candidate.candidate_id}.json" for candidate in spec.candidates}
    report_names = {f"{role}.json" for role in ROLES}
    for name, permitted in (("seals", seal_names), ("reports", report_names)):
        if {path.name for path in (root / name).iterdir()} - permitted:
            raise ValueError("unowned CPU report or seal evidence")
        for path in (root / name).iterdir():
            with regular_file(path):
                pass
    for role in ROLES:
        if (root / "reports" / f"{role}.json").exists() and not {f"{role}-{c}.json" for c in CONDITIONS} <= completed:
            raise ValueError("retained report is missing its inference evidence")
    if any(name.startswith("audit-") for name in claimed) and not (root / "confirmation.json").exists():
        raise ValueError("retained audit is missing its confirmation gate")
    if (root / "confirmation.json").exists():
        retained_gate = _read_record(root / "confirmation.json")
        if not {f"confirmation-{c}.json" for c in CONDITIONS} <= completed:
            raise ValueError("retained confirmation is missing inference evidence")
        if retained_gate.get("status") != "confirmed" and (any(n.startswith("audit-") for n in claimed)
                                                            or (root / "reports" / "audit.json").exists()):
            raise ValueError("retained audit violates confirmation veto")
    seals = {}
    for candidate in spec.candidates:
        seal_path = root / "seals" / f"{candidate.candidate_id}.json"
        if retained_claims or was_complete or seal_path.exists():
            if not seal_path.exists():
                raise ValueError("retained inference is missing its checkpoint seal")
            verified = registry.verify(candidate.handle)
        else:
            verified = registry.materialize(candidate.handle)
        verify_model_checkpoint(spec.model, registry.checkpoint_path(candidate.handle),
                                expected_sha256=candidate.output_sha256, expected_size_bytes=candidate.output_size_bytes)
        seal = {"schema_version": 1, "kind": "cpu_model_checkpoint_seal", "study_digest": spec.digest,
                "model_digest": spec.model.digest, "checkpoint": verified.to_dict(), "candidate_digest": candidate.digest}
        _immutable_json(seal_path, seal)
        seals[candidate.candidate_id] = canonical_digest(seal)
    selection = {"schema_version": 1, "kind": "fixed-before-inference", "study_digest": spec.digest,
                 "selected_candidate": spec.selected_candidate, "checkpoint_seals": seals,
                 "context": spec.context, "baseline_decoding": spec.baseline_decoding.to_dict(),
                 "alternative_decoding": spec.alternative_decoding.to_dict()}
    if (retained_claims or was_complete) and not (root / "selection.json").exists():
        raise ValueError("retained inference is missing frozen selection")
    _immutable_json(root / "selection.json", selection)
    spent = {"inference_calls": 0, "input_tokens": 0, "output_tokens": 0}
    receipts: dict[str, CpuBatchReceipt] = {}
    scores: dict[str, Any] = {}
    confirmation: dict[str, Any] | None = None
    expected_labels = set()
    for label, name, requests in batches:
        if label.startswith("audit-") and (confirmation is None or confirmation["status"] != "confirmed"):
            continue
        expected_labels.add(f"{label}.json")
        cost = _cost(requests)
        spent = {key: spent[key] + cost[key] for key in spent}
        _check_budget(spent, spec.budget)
        claim_path, result_path = root / "claims" / f"{label}.json", root / "results" / f"{label}.json"
        claim = {"schema_version": 1, "study_digest": spec.digest, "selection_digest": canonical_digest(selection),
                 "runtime_digest": runtime_digest, "checkpoint_seal_digest": seals[name],
                 "requests": [request.to_dict() for request in requests], "reserved": cost}
        registry.verify(_candidate(spec, name).handle)
        if claim_path.exists():
            _same(_read_record(claim_path), claim, "CPU batch claim differs from pinned work")
            if not result_path.exists():
                raise ValueError("incomplete CPU claim remains charged; refusing a second dispatch")
        else:
            if (result_path.exists() or was_complete
                    or ((root / "confirmation.json").exists() and not label.startswith("audit-"))):
                raise ValueError("missing retained CPU claim; refusing to recreate completed evidence")
            _immutable_json(claim_path, claim)
            receipt = run_cpu_batch(requests, checkpoint_path=registry.checkpoint_path(_candidate(spec, name).handle))
            _immutable_json(result_path, receipt.to_dict())
        receipt = _read_receipt(result_path, requests, runtime_digest)
        registry.verify(_candidate(spec, name).handle)
        receipts[label] = receipt
        role = "practice" if label.startswith("control-") else label.split("-")[0]
        cases = tuple(case for case in spec.cases if case.role == role)
        scores[label] = _score(cases, receipt)
        if label == "control-restored":
            original, restored = receipts["control-base"].results, receipt.results
            if [(row.text, row.logits) for row in original] != [(row.text, row.logits) for row in restored]:
                raise ValueError("restored checkpoint did not restore CPU behavior")
            if not (scores["control-helpful"]["accuracy"] > scores["control-base"]["accuracy"]
                    > scores["control-harmful"]["accuracy"]):
                raise ValueError("controlled helpful/harmful tensor edits did not change measured accuracy")
        if label.endswith("-D"):
            baseline_score = scores[f"{role}-A"]["accuracy"]
            report = {"schema_version": 1, "execution_kind": "cpu_inference", "model_execution_performed": True,
                      "gpu_execution_deferred": True, "promotion_eligible": False, "benchmark_result_claimed": False,
                      "role": role, "study_digest": spec.digest, "runtime_digest": runtime_digest,
                      "scorer": "exact-label-v1", "scorer_digest": spec.implementation_digest,
                      "conditions": {key: scores[f"{role}-{key}"] for key in CONDITIONS},
                      "contrasts": {f"{a}-{b}": scores[f"{role}-{a}"]["accuracy"] - scores[f"{role}-{b}"]["accuracy"]
                                    for a, b in (("C", "A"), ("W", "A"), ("CW", "C"), ("CW", "W"), ("D", "A"))},
                      "receipt_digests": {key: receipts[f"{role}-{key}"].semantic_digest for key in CONDITIONS}}
            report_path = root / "reports" / f"{role}.json"
            if was_complete and not report_path.exists():
                raise ValueError("completed CPU report is missing")
            _immutable_json(report_path, report)
            if role == "confirmation":
                outcomes = {key: scores[f"{role}-{key}"]["accuracy"] - baseline_score >= spec.minimum_confirmation_gain
                            for key in ("C", "W", "D")}
                confirmation = {"schema_version": 1, "look": 1, "selection_digest": canonical_digest(selection),
                                "report_digest": canonical_digest(report), "minimum_gain": spec.minimum_confirmation_gain,
                                "outcomes": outcomes, "status": "confirmed" if all(outcomes.values()) else "vetoed"}
                if was_complete and not (root / "confirmation.json").exists():
                    raise ValueError("completed confirmation evidence is missing")
                _immutable_json(root / "confirmation.json", confirmation)
    for name in ("claims", "results"):
        if {path.name for path in (root / name).iterdir()} != expected_labels:
            raise ValueError("unexpected retained inference evidence")
    reports = {"practice.json", "confirmation.json"}
    if confirmation and confirmation["status"] == "confirmed":
        reports.add("audit.json")
    if {path.name for path in (root / "reports").iterdir()} != reports:
        raise ValueError("audit must be withheld after veto; report inventory differs")
    if {path.name for path in (root / "seals").iterdir()} != {f"{c.candidate_id}.json" for c in spec.candidates}:
        raise ValueError("checkpoint seal inventory differs")
    summary = {"schema_version": 1, "study_digest": spec.digest, "execution_kind": "cpu_inference",
               "device": "cpu", "model_execution_performed": True, "gpu_execution_deferred": True,
               "promotion_eligible": False, "benchmark_result_claimed": False,
               "runtime_digest": runtime_digest, "selection_digest": canonical_digest(selection),
               "confirmation": confirmation, "audit_status": "completed" if "audit.json" in reports else "withheld",
               "reserved_usage": spent,
               "receipt_digests": {label: receipt.semantic_digest for label, receipt in receipts.items()},
               "controlled_accuracy": {name: scores[f"control-{name}"]["accuracy"]
                                       for name in ("base", "helpful", "harmful", "restored")},
               "scope": "constructed-correlated-tasks-not-benchmark-or-generalization-evidence"}
    summary["semantic_digest"] = canonical_digest(summary)
    summary["receipt_evidence_digests"] = {label: receipt.digest for label, receipt in receipts.items()}
    _immutable_json(root / "complete.json", summary)
    return summary


def run_cpu_study(directory: Path, *, output_dir: Path) -> dict[str, Any]:
    validate_cpu_study(directory)
    spec = load_cpu_study(directory)
    root = output_dir.absolute()
    _safe(root)
    root.mkdir(parents=True, exist_ok=True)
    lock = root / ".cpu-study.lock"
    if lock.exists() or lock.is_symlink():
        with regular_file(lock):
            pass
    with advisory_path_lock(lock):
        _clear_staging(root)
        return _run_locked(spec, directory, root)
