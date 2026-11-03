from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import pytest
from test_weight_cohort import EXAMPLE, replay_context

from autocontext.weight_evolution import evaluation, replay
from autocontext.weight_evolution.artifacts import canonical_digest
from autocontext.weight_evolution.docker_runner import WeightDockerRunner, WeightWorkerLimits


class FakeWorker:
    def __init__(self, context: SimpleNamespace, output: Path) -> None:
        self.context = context
        self.output = output
        limits = context.plan.limits
        self.limits = WeightWorkerLimits(timeout_seconds=limits.shard_timeout_seconds, memory_mb=limits.memory_mb,
                                         cpu_count=limits.cpu_count, max_report_bytes=limits.max_report_bytes)
        self.calls: list[str] = []
        self.fail_on_call: int | None = None
        self.fail_after_result = False
        self.wrong_preflight_field: str | None = None

    def plan(self, **options: Any) -> dict[str, Any]:
        result = WeightDockerRunner(limits=self.limits).plan(**options)
        if self.wrong_preflight_field:
            result[self.wrong_preflight_field] = "f" * 64
        return result

    def run(self, *, output_dir: Path, **options: Any) -> None:
        shard_id = output_dir.name
        request = json.loads(options["request_path"].read_bytes())
        claim = json.loads((self.output / "claims" / f"{shard_id}.json").read_bytes())
        shard = next(item for item in self.context.plan.shards if item.shard_id == shard_id)
        # The durable claim and exact projected request must precede any call.
        assert request == shard.request.to_dict()
        assert claim["plan_digest"] == self.context.plan.digest
        assert claim["request_digest"] == shard.request.digest
        assert claim["reserved_generated_tokens"] == 4 * len(shard.request.problems) * 16
        assert claim["reserved_worker_seconds"] == self.context.plan.limits.shard_timeout_seconds
        assert options["expected_harness_digest"] == json.loads((self.output / "harness.json").read_bytes())["harness_digest"]
        self.calls.append(shard_id)
        failing = self.fail_on_call == len(self.calls)
        if failing and not self.fail_after_result:
            raise RuntimeError("simulated worker failure")
        output_dir.mkdir()
        (output_dir / "report.json").write_text(json.dumps({"shard_id": shard_id, "request": request}))
        (output_dir / "execution.receipt.json").write_text(json.dumps({"shard_id": shard_id, "ok": True}))
        if failing:
            raise RuntimeError("simulated coordinator failure after result")


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    context = replay_context(count=2, mode="smoke")
    assets = tmp_path / "assets"
    assets.mkdir()
    checkpoints = {variant: tmp_path / variant for variant in ("base", "published_patch")}
    for checkpoint in checkpoints.values():
        checkpoint.mkdir()
    output = tmp_path / "replay"
    worker = FakeWorker(context, output)
    receipts = {receipt.variant: receipt for receipt in context.receipts}
    verifier = Mock(side_effect=lambda lock, root, directory, expected_variant: receipts[expected_variant])
    monkeypatch.setattr(replay, "verify_checkpoint", verifier)
    monkeypatch.setattr(replay, "worker_harness_digest", lambda: "9" * 64)

    def read_evidence(plan: Any, shard: Any, directory: Path) -> SimpleNamespace:
        report = (directory / "report.json").read_bytes()
        execution = (directory / "execution.receipt.json").read_bytes()
        if json.loads(report).get("shard_id") != shard.shard_id or json.loads(execution).get("ok") is not True:
            raise ValueError("tampered evidence")
        return SimpleNamespace(report_sha256=hashlib.sha256(report).hexdigest(),
                               execution_receipt_sha256=hashlib.sha256(execution).hexdigest(), harness_digest="9" * 64)

    evidence_reader = Mock(side_effect=read_evidence)
    monkeypatch.setattr(evaluation, "read_shard_evidence", evidence_reader)
    options = dict(datasets=(context.dataset,), lock_path=EXAMPLE / "reproduction.lock.json",
                   runtime_path=EXAMPLE / "runtime.lock.json", asset_root=assets, checkpoints=checkpoints,
                   output_dir=output, gpu_device="0", runner=worker)
    return SimpleNamespace(context=context, output=output, worker=worker, verifier=verifier,
                           evidence_reader=evidence_reader, options=options)


def run(workspace: SimpleNamespace) -> dict[str, Any]:
    return replay.run_replay(workspace.context.plan, **workspace.options)


def test_claims_precede_dispatch_and_complete_replay_resumes_without_any_calls(workspace: SimpleNamespace) -> None:
    result = run(workspace)
    assert result["dispatched_this_invocation"] == len(workspace.context.plan.shards)
    assert result["completed_shards"] == workspace.worker.calls
    for shard_id in result["completed_shards"]:
        assert (workspace.output / "claims" / f"{shard_id}.json").is_file()
        assert (workspace.output / "completed" / f"{shard_id}.json").is_file()
    before = list(workspace.worker.calls)
    second = run(workspace)
    assert second["dispatched_this_invocation"] == 0
    assert workspace.worker.calls == before
    assert second["max_generated_tokens"] == result["max_generated_tokens"]


def test_failed_dispatch_keeps_durable_claim_and_failure_receipt_and_cannot_auto_retry(workspace: SimpleNamespace) -> None:
    workspace.worker.fail_on_call = 1
    with pytest.raises(RuntimeError, match="simulated worker failure"):
        run(workspace)
    shard_id = workspace.context.plan.shards[0].shard_id
    failure = json.loads((workspace.output / "failures" / f"{shard_id}.json").read_bytes())
    assert failure["automatic_retry_permitted"] is False
    assert failure["status"] == "unreconciled"
    assert failure["elapsed_seconds"] >= 0
    assert not (workspace.output / "replay.receipt.json").exists()
    workspace.worker.fail_on_call = None
    with pytest.raises(replay.ReplayIncompleteError, match="do not auto-retry"):
        run(workspace)
    assert workspace.worker.calls == [shard_id]


def test_existing_complete_result_after_crash_is_reconciled_without_resending(workspace: SimpleNamespace) -> None:
    workspace.worker.fail_on_call = 1
    workspace.worker.fail_after_result = True
    with pytest.raises(RuntimeError, match="coordinator failure"):
        run(workspace)
    first = workspace.context.plan.shards[0].shard_id
    workspace.worker.fail_on_call = None
    result = run(workspace)
    assert workspace.worker.calls.count(first) == 1
    assert result["dispatched_this_invocation"] == len(workspace.context.plan.shards) - 1
    assert (workspace.output / "completed" / f"{first}.json").exists()


def test_partial_success_does_not_reset_prior_budget_or_dispatches(workspace: SimpleNamespace) -> None:
    workspace.worker.fail_on_call = 2
    with pytest.raises(RuntimeError):
        run(workspace)
    first, second = [shard.shard_id for shard in workspace.context.plan.shards[:2]]
    assert (workspace.output / "completed" / f"{first}.json").exists()
    assert not (workspace.output / "completed" / f"{second}.json").exists()
    with pytest.raises(replay.ReplayIncompleteError):
        run(workspace)
    assert workspace.worker.calls == [first, second]


@pytest.mark.parametrize("mutation", ["claim", "completion", "report", "execution", "plan"])
def test_tampered_saved_evidence_or_plan_is_rejected_without_dispatch_or_overwrite(
    workspace: SimpleNamespace, mutation: str,
) -> None:
    run(workspace)
    shard_id = workspace.context.plan.shards[0].shard_id
    paths = {
        "claim": workspace.output / "claims" / f"{shard_id}.json",
        "completion": workspace.output / "completed" / f"{shard_id}.json",
        "report": workspace.output / "runs" / shard_id / "report.json",
        "execution": workspace.output / "runs" / shard_id / "execution.receipt.json",
        "plan": workspace.output / "plan.json",
    }
    path = paths[mutation]
    original = json.loads(path.read_bytes())
    if mutation == "report":
        original["extra"] = "replacement"
    elif mutation == "execution":
        original["ok"] = False
    else:
        original["unexpected"] = True
    replacement = json.dumps(original).encode()
    path.write_bytes(replacement)
    calls = list(workspace.worker.calls)
    with pytest.raises((ValueError, replay.ReplayIncompleteError)):
        run(workspace)
    assert workspace.worker.calls == calls
    assert path.read_bytes() == replacement


def test_unclaimed_results_are_never_adopted(workspace: SimpleNamespace) -> None:
    workspace.worker.fail_on_call = 1
    workspace.worker.fail_after_result = True
    with pytest.raises(RuntimeError):
        run(workspace)
    first = workspace.context.plan.shards[0].shard_id
    (workspace.output / "claims" / f"{first}.json").unlink()
    with pytest.raises(replay.ReplayIncompleteError, match="unclaimed output"):
        run(workspace)
    assert len(workspace.worker.calls) == 1


def test_worker_resource_limits_must_match_before_output_creation(workspace: SimpleNamespace) -> None:
    workspace.worker.limits = WeightWorkerLimits(timeout_seconds=1.0)
    with pytest.raises(ValueError, match="worker resource limits"):
        run(workspace)
    assert not workspace.output.exists()
    assert workspace.worker.calls == []


@pytest.mark.parametrize("field", ["lock_digest", "runtime_digest", "request_digest"])
def test_changed_worker_preflight_identity_fails_before_claim(workspace: SimpleNamespace, field: str) -> None:
    workspace.worker.wrong_preflight_field = field
    with pytest.raises(ValueError, match="worker inputs changed"):
        run(workspace)
    assert list((workspace.output / "claims").iterdir()) == []
    assert workspace.worker.calls == []


def test_checkpoint_identity_mismatch_rejects_before_dispatch(workspace: SimpleNamespace) -> None:
    workspace.verifier.side_effect = None
    workspace.verifier.return_value = SimpleNamespace(digest="f" * 64, checkpoint_sha256="f" * 64,
                                                      tensor_content_digest="f" * 64)
    with pytest.raises(ValueError, match="checkpoint differs"):
        run(workspace)
    assert workspace.worker.calls == []
    assert not workspace.output.exists()


def test_output_symlink_rejected(workspace: SimpleNamespace, tmp_path: Path) -> None:
    target = tmp_path / "elsewhere"
    target.mkdir()
    workspace.output.symlink_to(target, target_is_directory=True)
    with pytest.raises(ValueError, match="symlinks"):
        run(workspace)
    assert list(target.iterdir()) == []


def test_replay_pins_worker_source_and_rejects_resume_after_source_change(
    workspace: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    run(workspace)
    assert json.loads((workspace.output / "harness.json").read_bytes()) == {"harness_digest": "9" * 64}
    calls = list(workspace.worker.calls)
    monkeypatch.setattr(replay, "worker_harness_digest", lambda: "8" * 64)
    with pytest.raises(replay.ReplayIncompleteError, match="source changed"):
        run(workspace)
    assert workspace.worker.calls == calls


@pytest.mark.parametrize("resuming", [False, True])
def test_result_from_different_worker_source_is_rejected(workspace: SimpleNamespace, resuming: bool) -> None:
    if resuming:
        run(workspace)
    reader = workspace.evidence_reader.side_effect

    def changed(plan: Any, shard: Any, directory: Path) -> SimpleNamespace:
        evidence = reader(plan, shard, directory)
        evidence.harness_digest = "8" * 64
        return evidence

    workspace.evidence_reader.side_effect = changed
    before = len(workspace.worker.calls)
    with pytest.raises(replay.ReplayIncompleteError, match="frozen replay harness"):
        run(workspace)
    assert len(workspace.worker.calls) == before + (0 if resuming else 1)


@pytest.mark.parametrize("directory", ["claims", "requests", "runs", "completed", "failures"])
def test_resumed_internal_symlink_directory_is_rejected(workspace: SimpleNamespace, directory: str, tmp_path: Path) -> None:
    run(workspace)
    internal = workspace.output / directory
    internal.rename(workspace.output / f"old-{directory}")
    target = tmp_path / "elsewhere"
    target.mkdir()
    internal.symlink_to(target, target_is_directory=True)
    calls = list(workspace.worker.calls)
    with pytest.raises(replay.ReplayIncompleteError, match="directory"):
        run(workspace)
    assert workspace.worker.calls == calls
    assert list(target.iterdir()) == []


def _accepted_execution(workspace: SimpleNamespace) -> dict[str, Any]:
    profile = {"harness_digest": "9" * 64, "runtime_environment": {"python_version": "3.12.1"},
               "hardware": {"accelerator": "cuda", "device_uuid": "GPU-01234567-89ab-cdef-0123-456789abcdef"}}
    reader = workspace.evidence_reader.side_effect

    def with_observations(plan: Any, shard: Any, directory: Path) -> SimpleNamespace:
        evidence = reader(plan, shard, directory)
        evidence.result = SimpleNamespace(runtime_environment=dict(profile["runtime_environment"]),
                                           hardware=dict(profile["hardware"]))
        return evidence

    workspace.evidence_reader.side_effect = with_observations
    workspace.options["expected_execution"] = profile
    return profile


def test_accepted_profile_persists_and_checks_every_shard_including_resume(workspace: SimpleNamespace) -> None:
    profile = _accepted_execution(workspace)
    first = run(workspace)
    assert first["accepted_execution_digest"] == canonical_digest(profile)
    assert json.loads((workspace.output / "accepted-execution.json").read_bytes()) == profile
    count = len(workspace.context.plan.shards)
    assert workspace.evidence_reader.call_count == count
    assert len(workspace.worker.calls) == count
    second = run(workspace)
    assert second["dispatched_this_invocation"] == 0
    assert second["accepted_execution_digest"] == first["accepted_execution_digest"]
    assert workspace.evidence_reader.call_count == count * 2
    assert len(workspace.worker.calls) == count


@pytest.mark.parametrize("field", ["runtime_environment", "hardware"])
def test_execution_drift_stops_before_next_shard_and_preserves_claim(workspace: SimpleNamespace, field: str) -> None:
    _accepted_execution(workspace)
    reader = workspace.evidence_reader.side_effect

    def changed(plan: Any, shard: Any, directory: Path) -> SimpleNamespace:
        evidence = reader(plan, shard, directory)
        if shard.shard_id == workspace.context.plan.shards[1].shard_id:
            setattr(evidence.result, field, {"changed": "observation"})
        return evidence

    workspace.evidence_reader.side_effect = changed
    with pytest.raises(replay.ReplayIncompleteError, match="differs from GPU acceptance"):
        run(workspace)
    first, second = [shard.shard_id for shard in workspace.context.plan.shards[:2]]
    assert workspace.worker.calls == [first, second]
    assert (workspace.output / "completed" / f"{first}.json").exists()
    assert not (workspace.output / "completed" / f"{second}.json").exists()
    assert (workspace.output / "claims" / f"{second}.json").exists()
    failure = json.loads((workspace.output / "failures" / f"{second}.json").read_bytes())
    assert failure["automatic_retry_permitted"] is False
    with pytest.raises(replay.ReplayIncompleteError, match="differs from GPU acceptance"):
        run(workspace)
    assert workspace.worker.calls == [first, second]


@pytest.mark.parametrize("change", ["omit", "hardware", "runtime", "saved_profile"])
def test_accepted_profile_cannot_be_downgraded_or_changed_on_resume(workspace: SimpleNamespace, change: str) -> None:
    profile = _accepted_execution(workspace)
    run(workspace)
    calls = list(workspace.worker.calls)
    if change == "omit":
        workspace.options.pop("expected_execution")
    elif change == "saved_profile":
        (workspace.output / "accepted-execution.json").write_text("{}")
    else:
        field = "hardware" if change == "hardware" else "runtime_environment"
        workspace.options["expected_execution"] = {**profile, field: {**profile[field], "changed": "value"}}
    with pytest.raises(replay.ReplayIncompleteError, match="accepted execution profile"):
        run(workspace)
    assert workspace.worker.calls == calls


@pytest.mark.parametrize("change", ["harness", "empty_runtime", "missing_uuid", "extra_field"])
def test_incomplete_accepted_profile_rejects_before_any_dispatch(workspace: SimpleNamespace, change: str) -> None:
    profile = _accepted_execution(workspace)
    if change == "harness":
        profile["harness_digest"] = "8" * 64
    elif change == "empty_runtime":
        profile["runtime_environment"] = {}
    elif change == "missing_uuid":
        profile["hardware"] = {"accelerator": "cuda"}
    else:
        profile["extra"] = True
    with pytest.raises(ValueError, match="accepted execution profile"):
        run(workspace)
    assert workspace.worker.calls == []
    assert not workspace.output.exists()
