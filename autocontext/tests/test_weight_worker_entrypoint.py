from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import pytest

from autocontext.weight_evolution import worker_entrypoint
from autocontext.weight_evolution.docker_runner import _copy_harness


@pytest.fixture
def worker(tmp_path: Path, monkeypatch: Any) -> SimpleNamespace:
    output = tmp_path / "output"
    output.mkdir()
    lock = SimpleNamespace(digest="a" * 64)
    runtime = SimpleNamespace(digest="b" * 64)
    observed = {"python_version": "3.12.3", "packages": {"vllm": "0.32.0"}, "platform": "linux/amd64"}
    request = SimpleNamespace(variant="published_patch", digest="c" * 64)
    mocks = {
        "load_reproduction_lock": Mock(return_value=lock),
        "load_runtime_lock": Mock(return_value=runtime),
        "check_installed_runtime": Mock(return_value=observed),
        "load_inference_request": Mock(return_value=request),
    }
    for name, value in mocks.items():
        monkeypatch.setattr(worker_entrypoint, name, value)
    reconstructed = SimpleNamespace(to_dict=lambda: {"variant": "published_patch", "checkpoint_sha256": "d" * 64})
    inferred = SimpleNamespace(to_dict=lambda: {"execution_kind": "vllm", "samples": [{"text": "answer"}]})
    reconstruct = Mock(return_value=reconstructed)
    infer = Mock(return_value=inferred)
    monkeypatch.setattr("autocontext.weight_evolution.patch_worker.reconstruct_checkpoint", reconstruct)
    monkeypatch.setattr("autocontext.weight_evolution.inference_worker.run_inference", infer)
    return SimpleNamespace(output=output, lock=lock, runtime=runtime, observed=observed, request=request,
                           reconstruct=reconstruct, infer=infer, mocks=mocks)


def arguments(worker: Any, operation: str = "reconstruct", budget: str = "1048576") -> list[str]:
    result = [operation, "--lock", "/input/lock.json", "--runtime", "/input/runtime.json",
              "--asset-root", "/assets", "--output", str(worker.output), "--variant", "published_patch",
              "--max-report-bytes", budget]
    if operation == "infer":
        result.extend(("--checkpoint", "/checkpoint", "--request", "/input/request.json"))
    return result


def test_worker_dispatches_reconstruction_and_records_observed_runtime(worker: Any, capsys: Any) -> None:
    assert worker_entrypoint.main(arguments(worker)) == 0
    worker.reconstruct.assert_called_once_with(worker.lock, Path("/assets"), worker.output / "checkpoint",
                                              variant="published_patch")
    worker.infer.assert_not_called()
    report = json.loads((worker.output / "report.json").read_text())
    assert report == {
        "ok": True, "operation": "reconstruct", "lock_digest": "a" * 64, "runtime_digest": "b" * 64,
        "observed_runtime": worker.observed,
        "checkpoint": {"variant": "published_patch", "checkpoint_sha256": "d" * 64},
    }
    assert capsys.readouterr().err == ""


def test_worker_dispatches_inference_with_bound_request(worker: Any) -> None:
    assert worker_entrypoint.main(arguments(worker, "infer")) == 0
    worker.infer.assert_called_once_with(worker.lock, Path("/assets"), Path("/checkpoint"), worker.request, worker.runtime)
    worker.reconstruct.assert_not_called()
    report = json.loads((worker.output / "report.json").read_text())
    assert report["request_digest"] == worker.request.digest
    assert report["inference"]["execution_kind"] == "vllm"
    assert report["inference"]["samples"] == [{"text": "answer"}]


def test_reconstruction_private_lock_is_not_exported_as_an_artifact(worker: Any) -> None:
    def reconstruct(_lock: Any, _assets: Path, output: Path, *, variant: str) -> SimpleNamespace:
        output.mkdir()
        # advisory_path_lock intentionally leaves its lock file after release.
        (output.parent / f".{output.name}.lock").touch()
        return SimpleNamespace(to_dict=lambda: {"variant": variant})

    worker.reconstruct.side_effect = reconstruct
    assert worker_entrypoint.main(arguments(worker)) == 0
    assert {path.name for path in worker.output.iterdir()} == {"report.json", "checkpoint"}


@pytest.mark.parametrize("budget", ["0", "-1", str(256 * 1024 * 1024 + 1)])
def test_invalid_report_limits_fail_before_runtime_or_dispatch(worker: Any, capsys: Any, budget: str) -> None:
    assert worker_entrypoint.main(arguments(worker, budget=budget)) == 2
    assert "invalid report byte budget" in json.loads(capsys.readouterr().err)["error"]
    worker.mocks["check_installed_runtime"].assert_not_called()
    worker.reconstruct.assert_not_called()
    assert not list(worker.output.iterdir())


def test_report_overflow_never_publishes_success(worker: Any, capsys: Any) -> None:
    assert worker_entrypoint.main(arguments(worker, "infer", budget="16")) == 2
    assert "evidence exceeds" in json.loads(capsys.readouterr().err)["error"]
    assert not (worker.output / "report.json").exists()


@pytest.mark.parametrize("target", ["runtime", "checkpoint", "inference"])
def test_failures_remain_infrastructure_errors_without_success_report(worker: Any, capsys: Any, target: str) -> None:
    failure = ValueError("deliberate fixture failure")
    if target == "runtime":
        worker.mocks["check_installed_runtime"].side_effect = failure
    elif target == "checkpoint":
        worker.reconstruct.side_effect = failure
    else:
        worker.infer.side_effect = failure
    operation = "infer" if target == "inference" else "reconstruct"
    assert worker_entrypoint.main(arguments(worker, operation)) == 2
    assert json.loads(capsys.readouterr().err) == {
        "ok": False, "operation": operation, "error": "ValueError: deliberate fixture failure",
    }
    assert not (worker.output / "report.json").exists()
    if target == "runtime":
        worker.reconstruct.assert_not_called()
        worker.infer.assert_not_called()


def test_variant_mismatch_cannot_dispatch_inference(worker: Any, capsys: Any) -> None:
    worker.request.variant = "base"
    assert worker_entrypoint.main(arguments(worker, "infer")) == 2
    assert "variant differs" in json.loads(capsys.readouterr().err)["error"]
    worker.infer.assert_not_called()


@pytest.mark.parametrize("condition", ["missing_checkpoint", "missing_request", "unexpected_request"])
def test_worker_rejects_mixed_or_incomplete_operation_inputs(worker: Any, capsys: Any, condition: str) -> None:
    argv = arguments(worker, "reconstruct" if condition == "unexpected_request" else "infer")
    if condition == "unexpected_request":
        argv.extend(("--request", "/input/request.json"))
    else:
        flag = "--checkpoint" if condition == "missing_checkpoint" else "--request"
        index = argv.index(flag)
        del argv[index:index + 2]
    assert worker_entrypoint.main(argv) == 2
    assert json.loads(capsys.readouterr().err)["ok"] is False
    worker.reconstruct.assert_not_called()
    worker.infer.assert_not_called()


@pytest.mark.parametrize("condition", ["nonempty", "symlink", "missing"])
def test_worker_requires_fresh_output_directory(worker: Any, capsys: Any, condition: str, tmp_path: Path) -> None:
    if condition == "nonempty":
        (worker.output / "existing.json").write_text("preserve")
    elif condition == "symlink":
        alias = tmp_path / "alias"
        alias.symlink_to(worker.output, target_is_directory=True)
        worker.output = alias
    else:
        worker.output.rmdir()
    assert worker_entrypoint.main(arguments(worker)) == 2
    assert "fresh empty directory" in json.loads(capsys.readouterr().err)["error"]
    worker.reconstruct.assert_not_called()
    if condition == "nonempty":
        assert (worker.output / "existing.json").read_text() == "preserve"


def test_copied_harness_imports_in_isolation_without_the_application(tmp_path: Path) -> None:
    harness = tmp_path / "harness"
    digest = _copy_harness(harness)
    assert len(digest) == 64
    code = "\n".join((
        "import importlib, pathlib, sys, types",
        f"root = {str(harness)!r}",
        # Confine autocontext imports to the copied namespace while retaining
        # third-party Pydantic. An editable install cannot fill missing modules.
        "package = types.ModuleType('autocontext')",
        "package.__path__ = [str(pathlib.Path(root) / 'autocontext')]",
        "sys.modules['autocontext'] = package",
        "for name in ('weight_evolution.worker_entrypoint', 'weight_evolution.patch_worker', "
        "'weight_evolution.inference_worker', 'util.models', 'util.file_lock'):",
        "    module = importlib.import_module('autocontext.' + name)",
        "    assert pathlib.Path(module.__file__).is_relative_to(root), module.__file__",
    ))
    result = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, cwd=tmp_path, check=False)
    assert result.returncode == 0, result.stderr
