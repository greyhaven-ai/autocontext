from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import pytest

from autocontext.execution.docker_isolation import DockerIsolationLimits, build_docker_isolation_command
from autocontext.weight_evolution import docker_runner
from autocontext.weight_evolution.artifacts import CheckpointReceipt
from autocontext.weight_evolution.inference_models import GenerationSample, InferenceResult, load_inference_request, text_digest
from autocontext.weight_evolution.inference_worker import sampling_parameters
from autocontext.weight_evolution.models import load_reproduction_lock
from autocontext.weight_evolution.runtime import load_runtime_lock

EXAMPLE = Path(__file__).resolve().parents[2] / "examples/weight_evolution/qwen_reproduction"
LOCK = EXAMPLE / "reproduction.lock.json"
RUNTIME = EXAMPLE / "runtime.lock.json"


@pytest.fixture
def workspace(tmp_path: Path) -> SimpleNamespace:
    # Resolve macOS's /var -> /private/var alias before exercising the explicit
    # no-symlink input-path policy.
    root = tmp_path.resolve()
    assets, checkpoint = root / "assets", root / "checkpoint"
    assets.mkdir()
    checkpoint.mkdir()
    request = root / "request.json"
    request.write_text(json.dumps({
        "variant": "published_patch", "dataset": "math500", "dataset_sha256": "a" * 64,
        "problems": [{"problem_id": "synthetic/1", "text": "What is one plus one?"}],
        "mode": "smoke", "smoke_max_new_tokens": 16,
    }))
    return SimpleNamespace(root=root, assets=assets, checkpoint=checkpoint, request=request, output=root / "result")


def _arguments(workspace: SimpleNamespace, *, infer: bool = False) -> dict[str, Any]:
    result = {
        "operation": "infer" if infer else "reconstruct", "lock_path": LOCK, "runtime_path": RUNTIME,
        "asset_root": workspace.assets, "output_dir": workspace.output,
    }
    if infer:
        result.update(checkpoint=workspace.checkpoint, request_path=workspace.request, gpu_device="GPU-test")
    return result


@pytest.fixture
def lifecycle(monkeypatch: pytest.MonkeyPatch, workspace: SimpleNamespace) -> SimpleNamespace:
    state = SimpleNamespace(command=None, mounts={}, report=None, raw_report=None, report_symlink=False,
                            exit_code=0, timed_out=False, quota=False, race_output=False)
    lock = load_reproduction_lock(LOCK)
    runtime = load_runtime_lock(RUNTIME)
    state.report = {
        "ok": True, "operation": "reconstruct", "lock_digest": lock.digest, "runtime_digest": runtime.digest,
    }
    state.checkpoint_receipt = CheckpointReceipt(
        variant="published_patch", lock_digest=lock.digest, checkpoint_sha256="d" * 64,
        tensor_content_digest="e" * 64, files=(), tensors=(), excluded_tensors=(), excluded_tensor_records=(),
        changed_entries=130188, selected_entries=131072,
    )
    state.report["checkpoint"] = state.checkpoint_receipt.to_dict()
    state.image = {"id": "sha256:" + "b" * 64, "repo_digests": [runtime.image], "platform": "linux/amd64"}
    monkeypatch.setattr(docker_runner.shutil, "which", lambda _binary: "/usr/bin/docker")
    monkeypatch.setattr(docker_runner.WeightDockerRunner, "_image_identity", lambda _self, _image: state.image)

    def create(command: list[str], **kwargs: Any) -> None:
        state.command = command
        state.mounts = {}
        for index, item in enumerate(command):
            if item == "--mount":
                pieces = dict(piece.split("=", 1) for piece in command[index + 1].split(",") if "=" in piece)
                state.mounts[pieces["dst"]] = Path(pieces["src"])

    def wait(**kwargs: Any) -> int:
        if state.timed_out:
            raise subprocess.TimeoutExpired("docker start", kwargs["timeout"])
        output = state.mounts["/output"]
        if state.report["operation"] == "reconstruct":
            (output / "checkpoint").mkdir()
        if state.report_symlink:
            target = workspace.root / "external-report.json"
            target.write_text(json.dumps(state.report))
            (output / "report.json").symlink_to(target)
        else:
            (output / "report.json").write_bytes(state.raw_report if state.raw_report is not None
                                                else json.dumps(state.report).encode())
        if state.race_output:
            workspace.output.mkdir()
        return state.exit_code

    def drains(*args: Any, **kwargs: Any) -> list[Any]:
        if state.quota:
            kwargs["quota_exceeded"].set()
        return []

    state.process = SimpleNamespace(wait=wait)
    state.watchdog = object()
    state.created = Mock(side_effect=create)
    state.removed = Mock()
    state.verified_removed = Mock()
    state.terminated = Mock()
    state.verifier = Mock(return_value=state.checkpoint_receipt)
    monkeypatch.setattr(docker_runner, "create_docker_container", state.created)
    state.watchdog_launch = Mock(return_value=state.watchdog)
    monkeypatch.setattr(docker_runner, "launch_deadline_watchdog", state.watchdog_launch)
    monkeypatch.setattr(docker_runner, "start_attached_docker_container", lambda *args, **kwargs: state.process)
    monkeypatch.setattr(docker_runner, "launch_bounded_output_drains", drains)
    monkeypatch.setattr(docker_runner, "remove_docker_container", state.removed)
    monkeypatch.setattr(docker_runner, "verify_docker_container_removed", state.verified_removed)
    monkeypatch.setattr(docker_runner, "terminate_process_group", state.terminated)
    monkeypatch.setattr("autocontext.weight_evolution.patch_worker.verify_checkpoint", state.verifier)
    return state


def _inference_report(workspace: SimpleNamespace, lifecycle: SimpleNamespace) -> None:
    lock, runtime = load_reproduction_lock(LOCK), load_runtime_lock(RUNTIME)
    request = load_inference_request(workspace.request)
    sample_text = "done</think>\\boxed{2}"
    samples = tuple(GenerationSample(
        problem_id="synthetic/1", sample_index=index, rendered_prompt="rendered",
        prompt_sha256=text_digest("rendered"), prompt_token_ids=(1, 2), input_tokens=2,
        text=sample_text, response_sha256=text_digest(sample_text), token_ids=(3, 4), output_tokens=2,
        finish_reason="stop", stop_reason=248044,
    ) for index in range(4))
    result = InferenceResult(
        execution_kind="vllm", lock_digest=lock.digest, runtime_digest=runtime.digest, runtime_image=runtime.image,
        runtime_environment={"python_version": "3.12.11", "packages": {"vllm": "0.32.0"}},
        checkpoint_receipt_digest=lifecycle.checkpoint_receipt.digest,
        checkpoint_sha256="d" * 64, tensor_content_digest="e" * 64,
        request_digest=request.digest, request=request, max_new_tokens=16, samples_per_problem=4,
        sampling=sampling_parameters(lock, request), elapsed_seconds=0.25,
        hardware={"accelerator": "cuda"}, samples=samples,
    )
    lifecycle.report.update(operation="infer", request_digest=request.digest, inference=result.to_dict())


def test_worker_harness_fingerprint_matches_copied_bytes_and_changes_with_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    package = tmp_path / "source"
    package.mkdir()
    source = package / "trusted.py"
    source.write_text("VALUE = 1\n")
    monkeypatch.setattr(docker_runner, "_harness_sources", lambda: (package, [source]))
    digest = docker_runner.worker_harness_digest()
    assert docker_runner._copy_harness(tmp_path / "copy") == digest
    assert (tmp_path / "copy" / "autocontext" / "trusted.py").read_bytes() == source.read_bytes()
    source.write_text("VALUE = 2\n")
    assert docker_runner.worker_harness_digest() != digest


def test_changed_copied_worker_harness_rejects_before_container_creation(
    workspace: SimpleNamespace, lifecycle: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(docker_runner, "_copy_harness", lambda destination: "e" * 64)
    with pytest.raises(ValueError, match="worker source changed"):
        docker_runner.WeightDockerRunner().run(expected_harness_digest="f" * 64, **_arguments(workspace))
    lifecycle.created.assert_not_called()
    assert not workspace.output.exists()


def test_matching_worker_harness_is_recorded_in_published_receipt(
    workspace: SimpleNamespace, lifecycle: SimpleNamespace,
) -> None:
    digest = docker_runner.worker_harness_digest()
    receipt = docker_runner.WeightDockerRunner().run(expected_harness_digest=digest, **_arguments(workspace))
    assert receipt["harness_digest"] == digest


def test_inference_dry_run_binds_request_budget_and_explicit_device(workspace: SimpleNamespace) -> None:
    arguments = _arguments(workspace, infer=True)
    arguments.pop("output_dir")
    plan = docker_runner.WeightDockerRunner(docker_binary="not-installed").plan(**arguments)
    assert plan["request_digest"] == load_inference_request(workspace.request).digest
    assert plan["max_generated_tokens"] == 64
    assert plan["samples"] == 4
    assert plan["mode"] == "smoke"
    assert plan["network"] == "none"
    assert plan["model_execution_performed"] is False
    assert plan["gpu_memory_isolation"] == "not_attested"


@pytest.mark.parametrize("device", [None, "all", "ALL", "0,1", "GPU-test\n", "GPU-test --privileged"])
def test_inference_plan_rejects_missing_or_ambiguous_device(workspace: SimpleNamespace, device: str | None) -> None:
    arguments = _arguments(workspace, infer=True)
    arguments.pop("output_dir")
    arguments["gpu_device"] = device
    with pytest.raises(ValueError):
        docker_runner.WeightDockerRunner().plan(**arguments)


@pytest.mark.parametrize("changed", ["request", "contract", "runtime"])
def test_changed_inputs_after_preflight_are_rejected_before_dispatch(
    workspace: SimpleNamespace, lifecycle: SimpleNamespace, monkeypatch: pytest.MonkeyPatch, changed: str,
) -> None:
    arguments = _arguments(workspace, infer=True)
    lock_copy = workspace.root / "reproduction.json"
    runtime_copy = workspace.root / "runtime.json"
    lock_copy.write_bytes(LOCK.read_bytes())
    runtime_copy.write_bytes(RUNTIME.read_bytes())
    arguments.update(lock_path=lock_copy, runtime_path=runtime_copy)
    target = {"request": workspace.request, "contract": lock_copy, "runtime": runtime_copy}[changed]

    def image_identity(_self: object, _image: str) -> dict[str, Any]:
        payload = json.loads(target.read_text())
        if changed == "request":
            payload["smoke_max_new_tokens"] = 32
        elif changed == "contract":
            payload["fidelity_gaps"].append("changed after preflight")
        else:
            payload["fidelity_notes"].append("changed after preflight")
        target.write_text(json.dumps(payload))
        return lifecycle.image

    monkeypatch.setattr(docker_runner.WeightDockerRunner, "_image_identity", image_identity)
    with pytest.raises(ValueError, match="changed after the resource plan"):
        docker_runner.WeightDockerRunner().run(**arguments)
    lifecycle.created.assert_not_called()
    assert not workspace.output.exists()


@pytest.mark.parametrize("change", ["digest", "architecture", "os", "missing"])
def test_image_identity_rejects_unavailable_or_wrong_image(monkeypatch: pytest.MonkeyPatch, change: str) -> None:
    image = load_runtime_lock(RUNTIME).image
    metadata = {"Id": "sha256:" + "b" * 64, "RepoDigests": [image], "Os": "linux", "Architecture": "amd64"}
    if change == "digest":
        metadata["RepoDigests"] = ["vllm/vllm-openai@sha256:" + "f" * 64]
    elif change == "architecture":
        metadata["Architecture"] = "arm64"
    elif change == "os":
        metadata["Os"] = "windows"
    inspect = Mock(return_value=SimpleNamespace(returncode=int(change == "missing"), stdout=json.dumps([metadata])))
    monkeypatch.setattr(docker_runner.subprocess, "run", inspect)
    with pytest.raises(RuntimeError):
        docker_runner.WeightDockerRunner()._image_identity(image)
    assert inspect.call_args.args[0] == ["docker", "image", "inspect", image]


def test_image_identity_retains_verified_content_id(monkeypatch: pytest.MonkeyPatch) -> None:
    image = load_runtime_lock(RUNTIME).image
    metadata = {"Id": "sha256:" + "b" * 64, "RepoDigests": [image], "Os": "linux", "Architecture": "amd64"}
    monkeypatch.setattr(docker_runner.subprocess, "run", lambda *a, **k: SimpleNamespace(
        returncode=0, stdout=json.dumps([metadata]),
    ))
    result = docker_runner.WeightDockerRunner()._image_identity(image)
    assert result == {"id": metadata["Id"], "repo_digests": [image], "platform": "linux/amd64"}


def test_success_publishes_complete_output_after_cleanup(
    workspace: SimpleNamespace, lifecycle: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HF_TOKEN", "must-not-cross")
    receipt = docker_runner.WeightDockerRunner().run(**_arguments(workspace))
    command = lifecycle.command
    assert command[command.index("--network") + 1] == "none"
    assert command[command.index("--entrypoint") + 1] == "/usr/bin/env"
    assert command[command.index("--pull") + 1] == "never"
    name = command[command.index("--name") + 1]
    assert f"ai.autocontext.weight-worker={name}" in command
    assert lifecycle.watchdog_launch.call_args.kwargs == {"owner_label": "ai.autocontext.weight-worker"}
    assert "--read-only" in command and "--gpus" not in command
    assert "must-not-cross" not in " ".join(command)
    mount_values = [command[index + 1] for index, part in enumerate(command) if part == "--mount"]
    assert all(value.endswith(",readonly") for value in mount_values if ",dst=/output" not in value)
    assert sum(not value.endswith(",readonly") for value in mount_values) == 1
    assert receipt["ok"] is True and receipt["model_execution_performed"] is False
    assert receipt["image_identity"] == lifecycle.image
    assert (workspace.output / "report.json").is_file()
    assert json.loads((workspace.output / "execution.receipt.json").read_text()) == receipt
    lifecycle.verifier.assert_called_once()
    lifecycle.removed.assert_called_once()
    lifecycle.verified_removed.assert_called_once()
    assert lifecycle.terminated.call_count == 2
    assert not list(workspace.root.glob(".weight-worker-*"))


@pytest.mark.parametrize("failure", ["exit", "timeout", "quota", "cleanup", "tensor_verification"])
def test_failed_worker_cleans_up_without_publishing(
    workspace: SimpleNamespace, lifecycle: SimpleNamespace, failure: str,
) -> None:
    if failure == "exit":
        lifecycle.exit_code = 4
    elif failure == "timeout":
        lifecycle.timed_out = True
    elif failure == "quota":
        lifecycle.quota = True
    elif failure == "cleanup":
        lifecycle.verified_removed.side_effect = RuntimeError("container still present")
    else:
        lifecycle.verifier.side_effect = ValueError("protected tensor modified")
    with pytest.raises((ValueError, RuntimeError, subprocess.TimeoutExpired)):
        docker_runner.WeightDockerRunner().run(**_arguments(workspace))
    assert not workspace.output.exists()
    lifecycle.removed.assert_called_once()
    if failure == "cleanup":
        # A failed daemon cleanup must leave the independent deadline owner
        # alive to retry, rather than abandoning a potentially live container.
        assert lifecycle.terminated.call_count == 1
        assert lifecycle.terminated.call_args.args[0] is lifecycle.process
    else:
        assert lifecycle.terminated.call_count == 2


@pytest.mark.parametrize("failure", ["not_ok", "lock", "runtime", "operation", "malformed", "duplicate", "oversized", "symlink"])
def test_invalid_report_never_publishes(
    workspace: SimpleNamespace, lifecycle: SimpleNamespace, failure: str,
) -> None:
    if failure == "not_ok":
        lifecycle.report["ok"] = False
    elif failure in {"lock", "runtime"}:
        lifecycle.report[f"{failure}_digest"] = "f" * 64
    elif failure == "operation":
        lifecycle.report["operation"] = "infer"
    elif failure == "malformed":
        lifecycle.raw_report = b"not-json"
    elif failure == "duplicate":
        lifecycle.raw_report = b'{"ok":true,"ok":true}'
    elif failure == "oversized":
        lifecycle.raw_report = b" " * 4097
    else:
        lifecycle.report_symlink = True
    runner = docker_runner.WeightDockerRunner(limits=docker_runner.WeightWorkerLimits(max_report_bytes=4096))
    with pytest.raises(ValueError):
        runner.run(**_arguments(workspace))
    assert not workspace.output.exists()
    lifecycle.removed.assert_called_once()


def test_forged_inference_envelope_without_raw_evidence_is_rejected(
    workspace: SimpleNamespace, lifecycle: SimpleNamespace,
) -> None:
    lifecycle.report.update(operation="infer", request_digest=load_inference_request(workspace.request).digest)
    with pytest.raises(ValueError):
        docker_runner.WeightDockerRunner().run(**_arguments(workspace, infer=True))
    assert not workspace.output.exists()
    command = lifecycle.command
    assert command[command.index("--gpus") + 1] == "device=GPU-test"


@pytest.mark.parametrize("change", ["missing", "different_hash"])
def test_reconstruction_report_must_match_independently_verified_checkpoint(
    workspace: SimpleNamespace, lifecycle: SimpleNamespace, change: str,
) -> None:
    if change == "missing":
        lifecycle.report.pop("checkpoint")
    else:
        lifecycle.report["checkpoint"]["checkpoint_sha256"] = "f" * 64
    with pytest.raises(ValueError, match="checkpoint"):
        docker_runner.WeightDockerRunner().run(**_arguments(workspace))
    assert not workspace.output.exists()
    lifecycle.verifier.assert_called_once()


def test_valid_inference_evidence_is_verified_and_published(workspace: SimpleNamespace, lifecycle: SimpleNamespace) -> None:
    _inference_report(workspace, lifecycle)
    receipt = docker_runner.WeightDockerRunner().run(**_arguments(workspace, infer=True))
    assert receipt["model_execution_performed"] is True
    assert receipt["request_digest"] == load_inference_request(workspace.request).digest
    assert json.loads((workspace.output / "report.json").read_text())["inference"]["benchmark_score"] is None
    assert lifecycle.verifier.call_args.kwargs == {"expected_variant": "published_patch"}
    command = lifecycle.command
    assert command[command.index("--gpus") + 1] == "device=GPU-test"
    checkpoint_mount = next(part for part in command if ",dst=/checkpoint" in part)
    assert checkpoint_mount.endswith(",readonly")


@pytest.mark.parametrize("change", ["fixture", "runtime_image", "checkpoint", "tensor_digest", "request", "missing_sample"])
def test_foreign_or_incomplete_inference_evidence_is_not_published(
    workspace: SimpleNamespace, lifecycle: SimpleNamespace, change: str,
) -> None:
    _inference_report(workspace, lifecycle)
    result = lifecycle.report["inference"]
    if change == "fixture":
        result["execution_kind"] = "fixture"
    elif change == "runtime_image":
        result["runtime_image"] = "different@sha256:" + "f" * 64
    elif change == "checkpoint":
        result["checkpoint_receipt_digest"] = "f" * 64
    elif change == "tensor_digest":
        result["tensor_content_digest"] = "f" * 64
    elif change == "request":
        result["request"]["problems"][0]["text"] = "A different problem"
    else:
        result["samples"].pop()
    with pytest.raises(ValueError):
        docker_runner.WeightDockerRunner().run(**_arguments(workspace, infer=True))
    assert not workspace.output.exists()


def test_existing_output_is_preserved_without_starting_worker(workspace: SimpleNamespace, lifecycle: SimpleNamespace) -> None:
    workspace.output.mkdir()
    marker = workspace.output / "keep.txt"
    marker.write_text("existing output")
    with pytest.raises(ValueError, match="already exist"):
        docker_runner.WeightDockerRunner().run(**_arguments(workspace))
    assert marker.read_text() == "existing output"
    lifecycle.created.assert_not_called()


def test_racing_empty_destination_is_not_overwritten(workspace: SimpleNamespace, lifecycle: SimpleNamespace) -> None:
    lifecycle.race_output = True
    with pytest.raises((ValueError, FileExistsError)):
        docker_runner.WeightDockerRunner().run(**_arguments(workspace))
    assert workspace.output.is_dir()
    assert not list(workspace.output.iterdir())


def test_interrupted_publication_has_no_completion_receipt(
    workspace: SimpleNamespace, lifecycle: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
) -> None:
    rename = docker_runner.os.rename

    def interrupted(source: Path, destination: Path) -> None:
        if Path(source).name == "execution.receipt.json":
            raise OSError("injected publication interruption")
        rename(source, destination)

    monkeypatch.setattr(docker_runner.os, "rename", interrupted)
    with pytest.raises(OSError, match="publication interruption"):
        docker_runner.WeightDockerRunner().run(**_arguments(workspace))
    assert workspace.output.is_dir()
    assert (workspace.output / "report.json").is_file()
    assert not (workspace.output / "execution.receipt.json").exists()


def test_harness_snapshot_contains_transitive_worker_dependencies(tmp_path: Path) -> None:
    docker_runner._copy_harness(tmp_path)
    assert (tmp_path / "autocontext/util/json_io.py").is_file()
    assert (tmp_path / "autocontext/util/file_lock.py").is_file()
    assert not list(tmp_path.rglob("*.pyc"))


@pytest.mark.parametrize("entrypoint", ["env", "/usr/bin/env --ignore-environment", "/bin/sh\n", "/bin/$(echo test)"])
def test_shared_builder_rejects_unsafe_entrypoint(entrypoint: str) -> None:
    with pytest.raises(ValueError, match="entrypoint"):
        build_docker_isolation_command(
            docker_binary="docker", image="example", container_name="test", labels={},
            limits=DockerIsolationLimits(memory_mb=1024, cpu_count=1.0, pids_limit=64),
            readonly_mounts={}, writable_mounts={}, tmpfs_mounts={}, argv=["python3"], entrypoint=entrypoint,
        )


def test_shared_builder_default_preserves_existing_entrypoint_behavior() -> None:
    command = build_docker_isolation_command(
        docker_binary="docker", image="example", container_name="test", labels={},
        limits=DockerIsolationLimits(memory_mb=1024, cpu_count=1.0, pids_limit=64),
        readonly_mounts={}, writable_mounts={}, tmpfs_mounts={}, argv=["python3"],
    )
    assert "--entrypoint" not in command
    assert command[-1] == "python3"
