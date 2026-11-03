"""Synthetic Docker envelopes test acceptance logic; no real CUDA is exercised."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from test_weight_cohort import EXAMPLE, replay_context

from autocontext.weight_evolution import acceptance, replay
from autocontext.weight_evolution.cohort import ReplayLimits, build_replay_plan
from autocontext.weight_evolution.datasets import DatasetInstruction, EvaluationProblem, VerifiedDataset, cohort_digest
from autocontext.weight_evolution.docker_runner import WeightDockerRunner
from autocontext.weight_evolution.inference_models import (
    GenerationSample,
    InferenceResult,
    load_inference_request,
    text_digest,
)
from autocontext.weight_evolution.inference_worker import sampling_parameters
from autocontext.weight_evolution.runtime import REQUIRED_PACKAGES

UUID = "GPU-11111111-2222-3333-4444-555555555555"


class SyntheticDockerWorker:
    def __init__(self, context: SimpleNamespace) -> None:
        self.context = context
        self.limits = acceptance._worker_limits(context.limits)
        self.calls: list[tuple[str, str]] = []
        self.fail_before = False
        self.fail_after = False
        self.kind = "vllm"
        self.empty = False
        self.wrong_uuid = False
        self.prompt_difference = False
        self.environment = {"python_version": "3.12.11", "platform": "linux/amd64",
                            "packages": {name: "1.0.0" for name in REQUIRED_PACKAGES}}
        self.environment["packages"]["vllm"] = "0.32.0"

    def plan(self, **options: Any) -> dict[str, Any]:
        return WeightDockerRunner(limits=self.limits).plan(**options)

    def run(self, *, output_dir: Path, expected_harness_digest: str, **options: Any) -> None:
        operation, variant = options["operation"], options["variant"]
        self.calls.append((operation, variant))
        if operation == "reconstruct":
            assert (self.context.output / "claims" / f"{variant}.json").is_file()
        if self.fail_before:
            raise RuntimeError("synthetic pre-publication failure")
        output_dir.mkdir()
        receipt = self.context.receipts[variant]
        report: dict[str, Any] = {"ok": True, "operation": operation,
                                  "lock_digest": self.context.lock.digest,
                                  "runtime_digest": self.context.runtime.digest,
                                  "observed_runtime": self.environment}
        if operation == "reconstruct":
            (output_dir / "checkpoint").mkdir()
            report["checkpoint"] = receipt.to_dict()
        else:
            request = load_inference_request(options["request_path"])
            samples = []
            for problem in request.problems:
                prompt = f"<user>{problem.text}</user><think>"
                if self.prompt_difference and variant == "published_patch":
                    prompt += "changed"
                for i in range(4 if request.dataset == "math500" else 2):
                    text = "" if self.empty else "Synthetic generation"
                    samples.append(GenerationSample(
                        problem_id=problem.problem_id, sample_index=i, rendered_prompt=prompt,
                        prompt_sha256=text_digest(prompt), prompt_token_ids=(1, 2), input_tokens=2,
                        text=text, response_sha256=text_digest(text), token_ids=() if self.empty else (3, 4),
                        output_tokens=0 if self.empty else 2, finish_reason="stop", stop_reason=248044,
                    ))
            result = InferenceResult(
                execution_kind=self.kind, lock_digest=self.context.lock.digest,
                runtime_digest=self.context.runtime.digest, runtime_image=self.context.runtime.image,
                runtime_environment=self.environment, checkpoint_receipt_digest=receipt.digest,
                checkpoint_sha256=receipt.checkpoint_sha256, tensor_content_digest=receipt.tensor_content_digest,
                request_digest=request.digest, request=request, max_new_tokens=request.smoke_max_new_tokens,
                samples_per_problem=4 if request.dataset == "math500" else 2,
                sampling=sampling_parameters(self.context.lock, request), elapsed_seconds=1,
                hardware={"accelerator": "cuda", "device_name": "synthetic GPU",
                          "device_uuid": "GPU-different" if self.wrong_uuid else UUID}, samples=tuple(samples),
            )
            report.update(request_digest=request.digest, inference=result.to_dict())
        (output_dir / "report.json").write_text(json.dumps(report))
        execution = {**self.plan(**options), "ok": True, "harness_digest": expected_harness_digest,
                     "elapsed_seconds": 1.0, "model_execution_performed": operation == "infer" and self.kind == "vllm",
                     "report_sha256": hashlib.sha256((output_dir / "report.json").read_bytes()).hexdigest(),
                     "image_identity": {"platform": "linux/amd64", "id": "sha256:" + "1" * 64,
                                        "repo_digests": [self.context.runtime.image]}}
        (output_dir / "execution.receipt.json").write_text(json.dumps(execution))
        if self.fail_after:
            raise RuntimeError("synthetic post-publication failure")


@pytest.fixture
def case(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    context = replay_context(count=2)
    datasets = [context.dataset]
    for name in ("gsm8k", "ifeval"):
        problem = EvaluationProblem(problem_id=f"{name}/0", text="Synthetic problem",
                                    answer="42" if name == "gsm8k" else None,
                                    instructions=(DatasetInstruction(instruction_id="keywords:existence",
                                                                     kwargs={"keywords": ["answer"]}),)
                                    if name == "ifeval" else ())
        datasets.append(VerifiedDataset(name, cohort_digest((problem,)), (problem,), "a" * 64, context.dataset.lock_digest))
    context.datasets = tuple(datasets)
    context.receipts = {r.variant: r for r in context.receipts}
    context.output = tmp_path / "acceptance"
    context.assets = tmp_path / "assets"
    context.assets.mkdir()
    context.worker = SyntheticDockerWorker(context)
    for module in (acceptance, replay):
        monkeypatch.setattr(module, "verify_checkpoint",
                            lambda lock, assets, path, expected_variant: context.receipts[expected_variant])
        monkeypatch.setattr(module, "worker_harness_digest", lambda: "9" * 64)
    context.options = dict(lock_path=EXAMPLE / "reproduction.lock.json", runtime_path=EXAMPLE / "runtime.lock.json",
                           asset_root=context.assets, datasets=context.datasets, scorer_lock_digest="c" * 64,
                           published_results_digest="d" * 64, output_dir=context.output, gpu_device="0",
                           limits=context.limits, smoke_max_new_tokens=16, expected_gpu_uuid=UUID, runner=context.worker)
    return context


def _summary(case: SimpleNamespace, **changes: Any) -> dict[str, Any]:
    options = {k: v for k, v in case.options.items() if k not in {"output_dir", "gpu_device", "expected_gpu_uuid", "runner"}}
    return acceptance.acceptance_resource_summary(**{**options, **changes})


def _verify(case: SimpleNamespace, **changes: Any) -> dict[str, Any]:
    plan = build_replay_plan(case.lock, case.runtime, case.datasets, tuple(case.receipts.values()),
                            scorer_lock_digest="c" * 64, published_results_digest="d" * 64, limits=case.limits)
    options = {k: v for k, v in case.options.items()
               if k in {"lock_path", "runtime_path", "asset_root", "datasets", "gpu_device"}}
    return acceptance.verify_gpu_acceptance(case.output, plan=plan, **{**options, **changes})


def test_dry_plan_is_bounded_and_performs_no_model_execution(case: SimpleNamespace) -> None:
    summary = _summary(case)
    assert summary["inference_workers"] == 6
    assert summary["reconstruction_workers"] == 2
    assert summary["max_generated_tokens"] == 16 * 16
    assert summary["reserved_worker_seconds"] == 8 * case.limits.shard_timeout_seconds
    assert summary["model_execution_performed"] is False
    assert not case.output.exists()
    assert not case.worker.calls


def test_live_envelope_acceptance_reconstructs_both_variants_then_six_smokes_and_resumes(case: SimpleNamespace) -> None:
    receipt = acceptance.run_gpu_acceptance(**case.options)
    assert case.worker.calls[:2] == [("reconstruct", "base"), ("reconstruct", "published_patch")]
    assert len(case.worker.calls) == 8
    assert receipt["hardware"]["device_uuid"] == UUID
    assert receipt["benchmark_reproduction_claimed"] is False
    assert receipt["runtime_environment"] == case.worker.environment
    assert acceptance.run_gpu_acceptance(**case.options) == receipt
    assert len(case.worker.calls) == 8
    assert _verify(case, expected_gpu_uuid=UUID) == receipt


@pytest.mark.parametrize("which", ["max_total_wall_seconds", "max_total_generated_tokens", "max_shard_generated_tokens"])
def test_insufficient_budget_rejected_before_any_output(case: SimpleNamespace, which: str) -> None:
    limits = ReplayLimits(**{**case.limits.to_dict(), which: 1})
    with pytest.raises(ValueError):
        acceptance.run_gpu_acceptance(**{**case.options, "limits": limits})
    assert not case.worker.calls and not case.output.exists()


def test_requires_all_three_datasets(case: SimpleNamespace) -> None:
    with pytest.raises(ValueError, match="all three"):
        _summary(case, datasets=(case.dataset,))


def test_failed_reconstruction_keeps_claim_and_refuses_retry(case: SimpleNamespace) -> None:
    case.worker.fail_before = True
    with pytest.raises(RuntimeError, match="pre-publication"):
        acceptance.run_gpu_acceptance(**case.options)
    case.worker.fail_before = False
    assert (case.output / "failures" / "base.json").exists()
    with pytest.raises(replay.ReplayIncompleteError, match="do not auto-retry"):
        acceptance.run_gpu_acceptance(**case.options)
    assert len(case.worker.calls) == 1


def test_published_reconstruction_is_reconciled_after_coordinator_crash(case: SimpleNamespace) -> None:
    case.worker.fail_after = True
    with pytest.raises(RuntimeError, match="post-publication"):
        acceptance.run_gpu_acceptance(**case.options)
    case.worker.fail_after = False
    receipt = acceptance.run_gpu_acceptance(**case.options)
    assert receipt["ok"] is True
    assert case.worker.calls.count(("reconstruct", "base")) == 1
    assert len(case.worker.calls) == 8


@pytest.mark.parametrize("field", ["kind", "empty", "wrong_uuid", "prompt_difference"])
def test_fixture_empty_or_wrong_device_cannot_pass_acceptance(case: SimpleNamespace, field: str) -> None:
    setattr(case.worker, field, "fixture" if field == "kind" else True)
    with pytest.raises(ValueError):
        acceptance.run_gpu_acceptance(**case.options)
    assert not (case.output / "acceptance.receipt.json").exists()


@pytest.mark.parametrize("path", ["acceptance.receipt.json", "completed/base.json", "smoke/completed/math500-00000-base.json",
                                  "smoke/claims/math500-00000-base.json"])
def test_changed_published_evidence_is_rejected(case: SimpleNamespace, path: str) -> None:
    acceptance.run_gpu_acceptance(**case.options)
    target = case.output / path
    value = json.loads(target.read_text())
    value["tampered"] = True
    target.write_text(json.dumps(value))
    with pytest.raises(ValueError):
        _verify(case)


def test_wrong_full_run_gpu_or_changed_harness_is_rejected(case: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    acceptance.run_gpu_acceptance(**case.options)
    with pytest.raises(ValueError, match="UUID"):
        _verify(case, expected_gpu_uuid="GPU-other")
    with pytest.raises(ValueError, match="selector"):
        _verify(case, gpu_device="1")
    monkeypatch.setattr(acceptance, "worker_harness_digest", lambda: "8" * 64)
    with pytest.raises(ValueError, match="harness"):
        _verify(case)


def test_wrong_scorer_or_checkpoint_cannot_use_acceptance(case: SimpleNamespace) -> None:
    acceptance.run_gpu_acceptance(**case.options)
    plan = build_replay_plan(case.lock, case.runtime, case.datasets, tuple(case.receipts.values()),
                            scorer_lock_digest="e" * 64, published_results_digest="d" * 64, limits=case.limits)
    with pytest.raises(ValueError, match="inputs"):
        acceptance.verify_gpu_acceptance(case.output, plan=plan, lock_path=EXAMPLE / "reproduction.lock.json",
                                         runtime_path=EXAMPLE / "runtime.lock.json", asset_root=case.assets,
                                         datasets=case.datasets)


def test_resume_does_not_adopt_unclaimed_reconstruction(case: SimpleNamespace) -> None:
    acceptance.run_gpu_acceptance(**case.options)
    (case.output / "claims" / "base.json").unlink()
    with pytest.raises(replay.ReplayIncompleteError, match="unclaimed"):
        acceptance.run_gpu_acceptance(**case.options)
    assert len(case.worker.calls) == 8


def test_output_symlink_is_rejected(case: SimpleNamespace, tmp_path: Path) -> None:
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    case.output.symlink_to(elsewhere, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        acceptance.run_gpu_acceptance(**case.options)
    assert not list(elsewhere.iterdir())


def test_host_identity_is_bound_to_acceptance_and_resume(case: SimpleNamespace) -> None:
    host = {"gpu_uuid": UUID, "driver_version": "580.1.2", "daemon_id": "daemon-id"}
    options = {**case.options, "host_identity": host}
    receipt = acceptance.run_gpu_acceptance(**options)
    assert receipt["host_identity"] == host
    assert _verify(case) == receipt
    assert acceptance.run_gpu_acceptance(**options) == receipt
    changed = {**host, "driver_version": "580.2.1"}
    with pytest.raises(replay.ReplayIncompleteError, match="inputs, GPU or harness changed"):
        acceptance.run_gpu_acceptance(**{**options, "host_identity": changed})
    assert len(case.worker.calls) == 8


@pytest.mark.parametrize("changed", [
    {"gpu_uuid": "GPU-aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"},
    {"driver_version": ""}, {"daemon_id": ""}, {"unexpected": "value"},
])
def test_invalid_host_identity_rejected_before_dispatch(case: SimpleNamespace, changed: dict[str, str]) -> None:
    host = {"gpu_uuid": UUID, "driver_version": "580.1.2", "daemon_id": "daemon-id", **changed}
    with pytest.raises(ValueError, match="host"):
        acceptance.run_gpu_acceptance(**{**case.options, "host_identity": host})
    assert not case.worker.calls and not case.output.exists()


def test_host_identity_matches_observed_cuda_even_without_expected_uuid(case: SimpleNamespace) -> None:
    host = {"gpu_uuid": "GPU-aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee", "driver_version": "580.1.2", "daemon_id": "daemon-id"}
    with pytest.raises(ValueError, match="observed GPU UUID differs from the acceptance host identity"):
        acceptance.run_gpu_acceptance(**{**case.options, "expected_gpu_uuid": None, "host_identity": host})
    assert not (case.output / "acceptance.receipt.json").exists()
