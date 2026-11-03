"""Durable practice-driven search using verified, actual CPU inference.

The finite search space is pinned, but scores and proposal decisions are not.
Only practice counts reach the local policy. A complete retained journal is
replayed before any new work, and uncertain or failed dispatches stay charged.
This coordinates trusted fixed code, not an arbitrary-code sandbox.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from autocontext.util.file_lock import advisory_path_lock
from autocontext.weight_evolution.artifacts import canonical_digest, file_identity, load_small_json, regular_file
from autocontext.weight_evolution.checkpoint_candidates import TensorCandidateRegistry, TensorCandidateSpec
from autocontext.weight_evolution.cpu_model import verify_model_checkpoint
from autocontext.weight_evolution.cpu_models import CpuInferenceRequest
from autocontext.weight_evolution.cpu_runner import CpuBatchReceipt, run_cpu_batch, runtime_identity, validate_cpu_receipt
from autocontext.weight_evolution.cpu_search_models import (
    MAX_SEARCH_BUNDLE_BYTES,
    SEARCH_MECHANISMS,
    CpuSearchBundleLock,
    CpuSearchMechanism,
    CpuSearchOption,
    CpuSearchSpec,
)
from autocontext.weight_evolution.cpu_search_policy import CpuPracticeScore, choose_next_option, select_best
from autocontext.weight_evolution.cpu_study import CONDITIONS, CpuCase, _score
from autocontext.weight_evolution.session import _clear_staging, _immutable_json, _read_record, _same

_METERS = ("proposals", "materializations", "inference_calls", "input_tokens", "output_tokens")
_DIRECTORIES = ("claims", "results", "seals", "reports")


class CpuSearchError(ValueError):
    """Search provenance, budget, or retained evidence is invalid."""


class CpuSearchBlockedError(CpuSearchError):
    """A claimed operation failed or has an uncertain outcome; never retry it."""


class _NeedExecution(Exception):
    """Read-only replay reached the first operation that has not been claimed."""


def search_implementation_digest() -> str:
    package = Path(__file__).parent
    names = ("cpu_search.py", "cpu_search_models.py", "cpu_search_policy.py", "cpu_study.py",
             "cpu_model.py", "cpu_models.py", "cpu_runner.py", "cpu_worker.py", "tensor_edits.py",
             "checkpoint_candidates.py", "artifacts.py", "models.py", "session.py", "session_models.py")
    return canonical_digest({name: file_identity(package / name) for name in names})


def _safe(path: Path) -> None:
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise CpuSearchError("CPU search paths must not traverse symlinks")


def load_cpu_search(directory: Path) -> CpuSearchSpec:
    _safe(directory.absolute())
    raw_lock = load_small_json(directory / "bundle.lock.json")
    lock = CpuSearchBundleLock.model_validate_json(json.dumps(raw_lock))
    _same(raw_lock, lock.to_dict(), "CPU search lock changed its exact typed representation")
    for name, digest, size in (("study.json", lock.spec_sha256, lock.spec_size_bytes),
                               ("base.safetensors", lock.base_sha256, lock.base_size_bytes)):
        actual = file_identity(directory / name, max_bytes=size)
        if actual["sha256"] != digest or actual["size_bytes"] != size:
            raise CpuSearchError("CPU search bundle byte/hash pin mismatch")
    raw = load_small_json(directory / "study.json", max_bytes=MAX_SEARCH_BUNDLE_BYTES)
    spec = CpuSearchSpec.model_validate_json(json.dumps(raw))
    _same(raw, spec.to_dict(), "CPU search spec changed its exact typed representation")
    if spec.implementation_digest != search_implementation_digest():
        raise CpuSearchError("CPU search implementation source changed")
    if spec.baseline.output_sha256 != lock.base_sha256 or spec.baseline.output_size_bytes != lock.base_size_bytes:
        raise CpuSearchError("CPU search baseline differs from the bundle pin")
    return spec


def _registry(spec: CpuSearchSpec, directory: Path, root: Path) -> TensorCandidateRegistry:
    return TensorCandidateRegistry(spec.candidates, input_path=directory / "base.safetensors",
                                   output_dir=root / "materializations")


def validate_cpu_search(directory: Path) -> dict[str, Any]:
    """Read-only preflight: no materialization, proposal, worker or output writes."""
    spec = load_cpu_search(directory)
    registry = _registry(spec, directory, directory / "unused-validation-store")
    registry.verify(spec.baseline.handle)
    verify_model_checkpoint(spec.model, directory / "base.safetensors",
                            expected_sha256=spec.baseline.output_sha256,
                            expected_size_bytes=spec.baseline.output_size_bytes)
    return {"study_digest": spec.digest, "candidate_count": len(spec.candidates),
            "required_usage": spec.required_usage, "rounds_per_mechanism": spec.rounds,
            "model_execution_performed": False, "gpu_execution_deferred": True,
            "scope": "validation-only-no-materialization-proposals-or-inference"}


def _cost(**values: int) -> dict[str, int]:
    return {name: values.get(name, 0) for name in _METERS}


class _Journal:
    def __init__(self, spec: CpuSearchSpec, root: Path, runtime_digest: str, *, replay: bool) -> None:
        self.spec, self.root, self.runtime_digest, self.replay = spec, root, runtime_digest, replay
        self.available = len(list((root / "claims").iterdir()))
        self.index = 0
        self.previous = "0" * 64
        self.spent = _cost()
        self.seen_artifacts: set[str] = set()
        self.was_complete = (root / "complete.json").exists()
        self.receipts: dict[str, CpuBatchReceipt] = {}
        self.materialized: set[str] = set()

    def perform(self, kind: str, label: str, inputs: dict[str, Any], reserved: dict[str, int],
                execute: Callable[[], dict[str, Any]], validate: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
        if set(reserved) != set(_METERS) or any(type(v) is not int or v < 0 for v in reserved.values()):
            raise CpuSearchError("CPU search reservation is invalid")
        spent = {name: self.spent[name] + reserved[name] for name in _METERS}
        if any(value > getattr(self.spec.budget, f"max_{name}") for name, value in spent.items()):
            raise CpuSearchError("CPU search exhausted its charged budget")
        claim = {"schema_version": 1, "study_digest": self.spec.digest, "runtime_digest": self.runtime_digest,
                 "index": self.index, "kind": kind, "label": label, "input": inputs, "reserved": reserved,
                 "previous_result_digest": self.previous}
        name = f"{self.index:08d}.json"
        claim_path, result_path = self.root / "claims" / name, self.root / "results" / name
        if self.index < self.available:
            _same(_read_record(claim_path), claim, "retained CPU search claim differs from replayed work")
            result = _read_record(result_path)
        else:
            if self.replay:
                raise _NeedExecution
            if self.was_complete:
                raise CpuSearchError("completed CPU search is missing a retained operation")
            _immutable_json(claim_path, claim)
            try:
                value = execute()
                validate(value)
            except Exception as exc:
                _immutable_json(result_path, {"operation_digest": canonical_digest(claim), "status": "failed",
                                              "error_type": type(exc).__name__})
                raise CpuSearchBlockedError(f"failed {label} remains charged; automatic retry is forbidden") from exc
            result = {"operation_digest": canonical_digest(claim), "status": "completed", "value": value}
            _immutable_json(result_path, result)
        if result.get("operation_digest") != canonical_digest(claim):
            raise CpuSearchError("CPU search result is not bound to its claim")
        if result.get("status") == "failed":
            if set(result) != {"operation_digest", "status", "error_type"} or not isinstance(result["error_type"], str):
                raise CpuSearchError("invalid failed CPU search result")
            raise CpuSearchBlockedError(f"failed {label} remains charged; automatic retry is forbidden")
        if set(result) != {"operation_digest", "status", "value"} or result["status"] != "completed":
            raise CpuSearchError("invalid completed CPU search result")
        value = result["value"]
        if not isinstance(value, dict):
            raise CpuSearchError("CPU search result value must be an object")
        validate(value)
        self.spent, self.previous = spent, canonical_digest(result)
        self.index += 1
        return value

    def publish(self, name: str, value: dict[str, Any]) -> None:
        self.seen_artifacts.add(name)
        path = self.root / name
        if path.exists():
            _same(_read_record(path), value, "CPU search derived evidence differs from its journal")
        elif self.was_complete or self.index < self.available:
            raise CpuSearchError("retained CPU search is missing an earlier seal, selection or report")
        elif not self.replay:
            _immutable_json(path, value)


def _candidate(spec: CpuSearchSpec, option: CpuSearchOption | None) -> TensorCandidateSpec:
    if option is None or option.candidate_id is None:
        return spec.baseline
    return next(candidate for candidate in spec.candidates if candidate.candidate_id == option.candidate_id)


def _probe(journal: _Journal, registry: TensorCandidateRegistry, cases: tuple[CpuCase, ...], label: str,
           *, weight: CpuSearchOption | None = None, context: CpuSearchOption | None = None,
           decoding: CpuSearchOption | None = None) -> tuple[CpuBatchReceipt, dict[str, Any]]:
    spec = journal.spec
    candidate = _candidate(spec, weight)
    prefix = context.context if context is not None else ""
    settings = decoding.decoding if decoding is not None else spec.baseline_decoding
    assert settings is not None
    requests = tuple(CpuInferenceRequest(
        request_id=f"{label}.{index}", model=spec.model, checkpoint_sha256=candidate.output_sha256,
        checkpoint_size_bytes=candidate.output_size_bytes, prompt=" ".join(filter(None, (prefix, case.prompt))),
        decoding=settings, limits=spec.limits,
    ) for index, case in enumerate(cases))
    inputs = {"requests": [request.to_dict() for request in requests], "case_ids": [case.case_id for case in cases],
              "candidate_digest": candidate.digest}

    def validate(raw: dict[str, Any]) -> None:
        registry.verify(candidate.handle)
        receipt = CpuBatchReceipt.model_validate_json(json.dumps(raw))
        _same(raw, receipt.to_dict(), "CPU receipt changed its typed representation")
        validate_cpu_receipt(receipt, requests, journal.runtime_digest)

    def execute() -> dict[str, Any]:
        registry.verify(candidate.handle)
        return run_cpu_batch(requests, checkpoint_path=registry.checkpoint_path(candidate.handle)).to_dict()

    raw = journal.perform("inference", label, inputs,
                          _cost(inference_calls=len(requests), output_tokens=len(requests),
                                input_tokens=sum(len(request.prompt.split()) for request in requests)),
                          execute, validate)
    receipt = CpuBatchReceipt.model_validate_json(json.dumps(raw))
    journal.receipts[label] = receipt
    return receipt, _score(cases, receipt)


def _practice_score(option: CpuSearchOption | None, score: dict[str, Any]) -> CpuPracticeScore:
    return CpuPracticeScore(option_id=option.option_id if option else None,
                            correct=sum(row["correct"] for row in score["cases"]), total=len(score["cases"]))


def _report(journal: _Journal, role: str, scores: dict[str, Any], receipts: dict[str, CpuBatchReceipt],
            selection_digest: str) -> dict[str, Any]:
    report = {"schema_version": 1, "execution_kind": "adaptive_cpu_inference", "model_execution_performed": True,
              "gpu_execution_deferred": True, "promotion_eligible": False, "benchmark_result_claimed": False,
              "role": role, "study_digest": journal.spec.digest, "runtime_digest": journal.runtime_digest,
              "selection_digest": selection_digest, "scorer": "exact-label-v1",
              "scorer_digest": journal.spec.implementation_digest,
              "conditions": scores,
              "contrasts": {f"{a}-{b}": scores[a]["accuracy"] - scores[b]["accuracy"]
                            for a, b in (("C", "A"), ("W", "A"), ("CW", "C"), ("CW", "W"), ("D", "A"))},
              "receipt_digests": {key: receipt.semantic_digest for key, receipt in receipts.items()}}
    journal.publish(f"reports/{role}.json", report)
    return report


def _workflow(journal: _Journal, registry: TensorCandidateRegistry) -> dict[str, Any]:
    spec = journal.spec
    seals: dict[str, str] = {}
    for candidate in spec.candidates:
        def verify_materialization(raw: dict[str, Any], candidate: TensorCandidateSpec = candidate) -> None:
            _same(raw, registry.verify(candidate.handle).to_dict(), "CPU checkpoint materialization changed")
            verify_model_checkpoint(spec.model, registry.checkpoint_path(candidate.handle),
                                    expected_sha256=candidate.output_sha256,
                                    expected_size_bytes=candidate.output_size_bytes)

        def materialize(candidate: TensorCandidateSpec = candidate) -> dict[str, Any]:
            return registry.materialize(candidate.handle).to_dict()

        verified = journal.perform("materialize", f"materialize-{candidate.candidate_id}",
                                   {"candidate": candidate.to_dict()}, _cost(materializations=1),
                                   materialize, verify_materialization)
        journal.materialized.add(candidate.digest)
        seal = {"schema_version": 1, "kind": "cpu_search_checkpoint_seal", "study_digest": spec.digest,
                "model_digest": spec.model.digest, "checkpoint": verified}
        journal.publish(f"seals/{candidate.candidate_id}.json", seal)
        seals[candidate.candidate_id] = canonical_digest(seal)
    practice = tuple(case for case in spec.cases if case.role == "practice")
    histories: dict[str, list[dict[str, Any]]] = {}
    proposal_history: dict[str, list[dict[str, Any]]] = {}
    search_usage: dict[str, dict[str, int]] = {}
    chosen: dict[str, CpuSearchOption | None] = {}
    selections: dict[str, Any] = {}
    practice_scores: dict[str, Any] = {}
    practice_receipts: dict[str, CpuBatchReceipt] = {}
    for mechanism in SEARCH_MECHANISMS:
        before_search = journal.spent.copy()
        proposal_history[mechanism] = []
        baseline, baseline_score = _probe(journal, registry, practice, f"{mechanism}-baseline")
        if mechanism == "C":
            practice_scores["A"], practice_receipts["A"] = baseline_score, baseline
        history: tuple[CpuPracticeScore, ...] = (_practice_score(None, baseline_score),)
        measured: dict[str | None, CpuBatchReceipt] = {None: baseline}
        for trial in range(1, spec.rounds + 1):
            options = spec.options_for(mechanism)
            inputs = {"mechanism": mechanism, "options": [option.to_dict() for option in options],
                      "history": [row.to_dict() for row in history],
                      "practice_receipt_digests": [receipt.semantic_digest for receipt in measured.values()]}

            def propose(options: tuple[CpuSearchOption, ...] = options,
                        history: tuple[CpuPracticeScore, ...] = history) -> dict[str, Any]:
                return choose_next_option(options, history).to_dict()

            decision = journal.perform("proposal", f"{mechanism}-proposal-{trial}", inputs, _cost(proposals=1),
                                       propose, lambda raw: _same(raw, propose(), "CPU proposal differs from practice feedback"))
            proposal_history[mechanism].append(decision)
            option = next(item for item in options if item.option_id == decision["option_id"])
            receipt, score = _arm_probe(journal, registry, practice, f"{mechanism}-trial-{trial}", mechanism, option)
            measured[option.option_id] = receipt
            history = (*history, _practice_score(option, score))
        best = select_best(history)
        winner = next((item for item in spec.options_for(mechanism) if item.option_id == best.option_id), None)
        chosen[mechanism] = winner
        # Restoring means selecting and independently verifying the earlier
        # immutable checkpoint/context/decoder, then performing a charged probe.
        restored, score = _arm_probe(journal, registry, practice, f"{mechanism}-restored", mechanism, chosen[mechanism])
        original = measured[best.option_id]
        before = [(row.input_tokens, row.logits, row.output_token_id, row.text) for row in original.results]
        after = [(row.input_tokens, row.logits, row.output_token_id, row.text) for row in restored.results]
        if before != after or _practice_score(chosen[mechanism], score) != best:
            raise CpuSearchError("CPU restoration changed the selected candidate's measured behavior")
        histories[mechanism] = [row.to_dict() for row in history]
        selections[mechanism] = {"option_id": best.option_id, "score": best.to_dict(),
                                 "option": winner.to_dict() if winner else None,
                                 "history_digest": canonical_digest(histories[mechanism]),
                                 "restored_receipt_digest": restored.semantic_digest}
        practice_scores[mechanism], practice_receipts[mechanism] = score, restored
        search_usage[mechanism] = {name: journal.spent[name] - before_search[name] for name in _METERS}
    selection = {"schema_version": 1, "kind": "practice-selected-before-confirmation", "study_digest": spec.digest,
                 "selections": selections, "checkpoint_seals": seals,
                 "selection_rule": "highest-practice-correct-then-earliest-round", "evaluation_seed": spec.evaluation_seed,
                 "composition": "selected-C-plus-selected-W-no-extra-search"}
    journal.publish("selection.json", selection)
    selection_digest = canonical_digest(selection)
    cw, cw_score = _probe(journal, registry, practice, "practice-CW", weight=chosen["W"], context=chosen["C"])
    practice_scores["CW"], practice_receipts["CW"] = cw_score, cw
    _report(journal, "practice", practice_scores, practice_receipts, selection_digest)
    confirmation: dict[str, Any] | None = None
    for role in ("confirmation", "audit"):
        if role == "audit" and (confirmation is None or confirmation["status"] != "confirmed"):
            break
        cases = tuple(case for case in spec.cases if case.role == role)
        scores: dict[str, Any] = {}
        receipts: dict[str, CpuBatchReceipt] = {}
        for condition in CONDITIONS:
            receipts[condition], scores[condition] = _probe(
                journal, registry, cases, f"{role}-{condition}",
                weight=chosen["W"] if condition in {"W", "CW"} else None,
                context=chosen["C"] if condition in {"C", "CW"} else None,
                decoding=chosen["D"] if condition == "D" else None,
            )
        report = _report(journal, role, scores, receipts, selection_digest)
        if role == "confirmation":
            outcomes = {key: scores[key]["accuracy"] - scores["A"]["accuracy"] >= spec.minimum_confirmation_gain
                        for key in SEARCH_MECHANISMS}
            confirmation = {"schema_version": 1, "look": 1, "selection_digest": selection_digest,
                            "report_digest": canonical_digest(report), "minimum_gain": spec.minimum_confirmation_gain,
                            "outcomes": outcomes, "status": "confirmed" if all(outcomes.values()) else "vetoed"}
            journal.publish("confirmation.json", confirmation)
    assert confirmation is not None
    prefix = max(len((option.context or "").split()) for option in spec.options_for("C"))
    capacity = _cost(proposals=spec.rounds, inference_calls=(spec.rounds + 2) * len(practice),
                     output_tokens=(spec.rounds + 2) * len(practice),
                     input_tokens=(spec.rounds + 2) * sum(len(case.prompt.split()) + prefix for case in practice))
    summary = {"schema_version": 1, "execution_kind": "adaptive_cpu_inference", "device": "cpu",
               "model_execution_performed": True, "gpu_execution_deferred": True,
               "promotion_eligible": False, "benchmark_result_claimed": False,
               "study_digest": spec.digest, "runtime_digest": journal.runtime_digest,
               "selection_digest": selection_digest, "histories": histories,
               "proposal_history": proposal_history, "search_usage": search_usage,
               "search_capacity_per_mechanism": capacity,
               "selected_options": {key: value.option_id if value else None for key, value in chosen.items()},
               "restoration_verified": {key: True for key in SEARCH_MECHANISMS}, "confirmation": confirmation,
               "audit_status": "completed" if confirmation["status"] == "confirmed" else "withheld",
               "reserved_usage": journal.spent,
               "receipt_digests": {key: receipt.semantic_digest for key, receipt in journal.receipts.items()},
               "scope": "constructed-correlated-tasks-not-benchmark-or-generalization-evidence"}
    summary["semantic_digest"] = canonical_digest(summary)
    summary["receipt_evidence_digests"] = {key: receipt.digest for key, receipt in journal.receipts.items()}
    summary["journal_digest"] = journal.previous
    journal.publish("complete.json", summary)
    return summary


def _arm_probe(journal: _Journal, registry: TensorCandidateRegistry, cases: tuple[CpuCase, ...], label: str,
               mechanism: CpuSearchMechanism, option: CpuSearchOption | None) -> tuple[CpuBatchReceipt, dict[str, Any]]:
    return _probe(journal, registry, cases, label, weight=option if mechanism == "W" else None,
                  context=option if mechanism == "C" else None, decoding=option if mechanism == "D" else None)


def _artifact_inventory(root: Path) -> set[str]:
    return {name for name in ("selection.json", "confirmation.json", "complete.json") if (root / name).exists()} | {
        f"{directory}/{path.name}" for directory in ("seals", "reports") for path in (root / directory).iterdir()
    }


def _prepare(spec: CpuSearchSpec, root: Path, runtime: dict[str, Any]) -> dict[str, Any]:
    allowed = {".cpu-search.lock", "manifest.json", "materializations", *_DIRECTORIES,
               "selection.json", "confirmation.json", "complete.json"}
    entries = {path.name for path in root.iterdir()}
    if entries - allowed or ("manifest.json" not in entries and entries - {".cpu-search.lock"}):
        raise CpuSearchError("CPU search directory contains unowned evidence")
    for path in root.iterdir():
        _safe(path)
        if path.name not in {*_DIRECTORIES, "materializations"}:
            with regular_file(path):
                pass
    manifest = {"schema_version": 1, "study": spec.to_dict(), "study_digest": spec.digest,
                "runtime": runtime, "runtime_digest": canonical_digest(runtime)}
    if "manifest.json" in entries:
        _same(_read_record(root / "manifest.json"), manifest, "CPU search manifest or runtime changed")
    for name in _DIRECTORIES:
        path = root / name
        if "complete.json" in entries and not path.is_dir():
            raise CpuSearchError("completed CPU search is missing retained evidence")
        path.mkdir(exist_ok=True)
        _clear_staging(path)
        for entry in path.iterdir():
            with regular_file(entry):
                pass
    claimed = {path.name for path in (root / "claims").iterdir()}
    results = {path.name for path in (root / "results").iterdir()}
    if claimed != {f"{index:08d}.json" for index in range(len(claimed))} or not results <= claimed:
        raise CpuSearchError("CPU search claims/results are not an ordered prefix")
    if results != claimed:
        raise CpuSearchBlockedError("incomplete CPU search claim remains charged; automatic retry is forbidden")
    return manifest


def run_cpu_search(directory: Path, *, output_dir: Path) -> dict[str, Any]:
    validate_cpu_search(directory)
    spec = load_cpu_search(directory)
    root = output_dir.absolute()
    _safe(root)
    root.mkdir(parents=True, exist_ok=True)
    lock = root / ".cpu-search.lock"
    if lock.exists() or lock.is_symlink():
        with regular_file(lock):
            pass
    with advisory_path_lock(lock):
        _clear_staging(root)
        runtime = runtime_identity()
        manifest = _prepare(spec, root, runtime)
        registry = _registry(spec, directory, root)
        materialized = {item.reference.candidate_digest for item in registry.verify_existing()}
        replay = _Journal(spec, root, canonical_digest(runtime), replay=True)
        complete: dict[str, Any] | None = None
        try:
            complete = _workflow(replay, registry)
        except _NeedExecution:
            if replay.was_complete:
                raise CpuSearchError("completed CPU search is missing journal evidence") from None
        if replay.index != replay.available or _artifact_inventory(root) - replay.seen_artifacts:
            raise CpuSearchError("CPU search retains evidence beyond its verified execution prefix")
        if materialized != replay.materialized | {spec.baseline.digest}:
            raise CpuSearchError("CPU search retains an unclaimed checkpoint materialization")
        if complete is not None and replay.was_complete:
            return complete
        _immutable_json(root / "manifest.json", manifest)
        journal = _Journal(spec, root, canonical_digest(runtime), replay=False)
        result = _workflow(journal, registry)
        if _artifact_inventory(root) != journal.seen_artifacts:
            raise CpuSearchError("CPU search derived evidence inventory differs")
        return result
