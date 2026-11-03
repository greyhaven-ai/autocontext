"""Example command boundaries stay explicit; fixtures never dispatch CUDA."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

import pytest
from test_weight_acceptance import UUID
from test_weight_cohort import EXAMPLE, replay_context

from autocontext.weight_evolution import gpu_commands
from autocontext.weight_evolution.cohort import build_replay_plan
from autocontext.weight_evolution.datasets import DatasetInstruction, EvaluationProblem, VerifiedDataset, cohort_digest
from autocontext.weight_evolution.reference import load_published_results
from autocontext.weight_evolution.scorer_runtime import load_scorer_lock


@pytest.fixture
def entrypoint() -> ModuleType:
    spec = importlib.util.spec_from_file_location("weight_gpu_example", EXAMPLE / "run.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def context(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    base = replay_context(count=2)
    datasets = [base.dataset]
    for name in ("gsm8k", "ifeval"):
        problem = EvaluationProblem(problem_id=f"{name}/0", text="Synthetic question",
                                    answer="42" if name == "gsm8k" else None,
                                    instructions=(DatasetInstruction(instruction_id="keywords:existence",
                                                                     kwargs={"keywords": ["answer"]}),)
                                    if name == "ifeval" else ())
        datasets.append(VerifiedDataset(name, cohort_digest((problem,)), (problem,), "b" * 64, base.dataset.lock_digest))
    base.datasets = tuple(datasets)
    base.scorer_lock = load_scorer_lock(EXAMPLE / "scorer.lock.json")
    base.published = load_published_results(EXAMPLE / "published-results.lock.json")
    base.plan = build_replay_plan(base.lock, base.runtime, base.datasets, base.receipts,
                                  scorer_lock_digest=base.scorer_lock.digest,
                                  published_results_digest=base.published.digest, limits=base.limits)
    base.root = tmp_path
    base.plan_path = tmp_path / "plan.json"
    base.plan_path.write_text(json.dumps(base.plan.to_dict()))
    base.dataset_loader = Mock(return_value=SimpleNamespace(digest=base.plan.dataset_lock_digest))
    base.dataset_verifier = Mock(return_value=base.datasets)
    base.readiness = Mock(return_value={"ready_for_acceptance": True,
                                       "observations": {"selected_gpu": {"uuid": UUID, "driver_version": "580.65.06"},
                                                        "daemon": {"id": "synthetic-docker-daemon"}}})
    base.scorer = Mock(execution_kind="official", lock_digest=base.scorer_lock.digest)
    base.scorer_loader = Mock(return_value=base.scorer)
    base.acceptance_runner = Mock(return_value={"ok": True, "scope": "gpu_inference_acceptance"})
    base.reproduction_runner = Mock(return_value={"ok": True, "executed": True, "scope": "accepted_gpu_full_reproduction"})
    monkeypatch.setattr(gpu_commands, "load_dataset_lock", base.dataset_loader)
    monkeypatch.setattr(gpu_commands, "verify_datasets", base.dataset_verifier)
    monkeypatch.setattr(gpu_commands, "probe_gpu_readiness", base.readiness)
    monkeypatch.setattr(gpu_commands, "load_pinned_scorer", base.scorer_loader)
    monkeypatch.setattr(gpu_commands, "run_gpu_acceptance", base.acceptance_runner)
    monkeypatch.setattr(gpu_commands, "run_reproduction", base.reproduction_runner)
    return base


def arguments(context: SimpleNamespace, command: str) -> list[str]:
    result = [command, "--asset-root", str(context.root / "assets"), "--dataset-root", str(context.root / "datasets"),
              "--scorer-root", str(context.root / "scorers"), "--output", str(context.root / "output"), "--gpu-device", "0"]
    if command == "accept-gpu":
        result += ["--max-total-generated-tokens", "1024", "--max-total-wall-seconds", "14400"]
    else:
        result += ["--plan", str(context.plan_path), "--acceptance", str(context.root / "acceptance")]
    return result


@pytest.mark.parametrize("command", ["accept-gpu", "run-reproduction"])
def test_dry_commands_validate_inputs_but_do_not_probe_scorers_or_containers(
    entrypoint: ModuleType, context: SimpleNamespace, capsys: pytest.CaptureFixture[str], command: str,
) -> None:
    assert entrypoint.main(arguments(context, command)) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["ok"] is True and result["executed"] is False
    assert result["model_execution_performed"] is False
    if command == "run-reproduction":
        assert result["acceptance_verified"] is False
        assert result["acceptance_required"] is True
    else:
        assert result["reconstruction_workers"] == 2 and result["inference_workers"] == 6
    context.dataset_verifier.assert_called_once()
    for mock in (context.readiness, context.scorer_loader, context.acceptance_runner, context.reproduction_runner):
        mock.assert_not_called()
    assert not (context.root / "output").exists()


def test_acceptance_blocked_readiness_returns_exit_one_without_loading_scorer_or_dispatching(
    entrypoint: ModuleType, context: SimpleNamespace, capsys: pytest.CaptureFixture[str],
) -> None:
    context.readiness.return_value = {"ready_for_acceptance": False, "blockers": ["synthetic no NVIDIA host"]}
    assert entrypoint.main([*arguments(context, "accept-gpu"), "--execute"]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["ok"] is False and result["executed"] is False and result["stage"] == "gpu_readiness"
    context.scorer_loader.assert_not_called()
    context.acceptance_runner.assert_not_called()


def test_acceptance_preflights_every_dataset_before_dispatch_and_passes_resolved_uuid(
    entrypoint: ModuleType, context: SimpleNamespace, capsys: pytest.CaptureFixture[str],
) -> None:
    events = []
    context.scorer.validate_cohort.side_effect = lambda dataset, problems: events.append(dataset)

    def dispatch(**options):
        assert events == ["math500", "gsm8k", "ifeval"]
        assert options["gpu_device"] == options["expected_gpu_uuid"] == UUID
        assert options["host_identity"] == {"gpu_uuid": UUID, "driver_version": "580.65.06",
                                            "daemon_id": "synthetic-docker-daemon"}
        assert options["datasets"] == context.datasets
        events.append("dispatch")
        return {"ok": True}

    context.acceptance_runner.side_effect = dispatch
    assert entrypoint.main([*arguments(context, "accept-gpu"), "--execute"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["executed"] is True
    assert events[-1] == "dispatch"
    context.scorer_loader.assert_called_once_with(context.scorer_lock, context.root / "scorers")


def test_scorer_preflight_failure_prevents_acceptance_dispatch(
    entrypoint: ModuleType, context: SimpleNamespace, capsys: pytest.CaptureFixture[str],
) -> None:
    context.scorer.validate_cohort.side_effect = ValueError("synthetic invalid gold")
    assert entrypoint.main([*arguments(context, "accept-gpu"), "--execute"]) == 2
    captured = capsys.readouterr()
    assert captured.out == "" and "invalid gold" in captured.err
    context.acceptance_runner.assert_not_called()


def test_explicit_full_execution_is_delegated_to_acceptance_gated_coordinator(
    entrypoint: ModuleType, context: SimpleNamespace, capsys: pytest.CaptureFixture[str],
) -> None:
    assert entrypoint.main([*arguments(context, "run-reproduction"), "--execute"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["executed"] is True
    call = context.reproduction_runner.call_args
    assert call.args == (context.plan,)
    assert call.kwargs["acceptance_dir"] == context.root / "acceptance"
    assert call.kwargs["scorer"] is context.scorer
    assert call.kwargs["published"] == context.published
    assert call.kwargs["gpu_device"] == "0"
    # The coordinator owns actual readiness, acceptance revalidation and dispatch.
    context.readiness.assert_not_called()
    context.acceptance_runner.assert_not_called()


@pytest.mark.parametrize("mode", ["smoke", "subset"])
def test_full_run_dry_rejects_smoke_or_incomplete_dataset_selection(
    entrypoint: ModuleType, context: SimpleNamespace, capsys: pytest.CaptureFixture[str], mode: str,
) -> None:
    datasets = context.datasets if mode == "smoke" else (context.dataset,)
    plan = build_replay_plan(context.lock, context.runtime, datasets, context.receipts,
                            scorer_lock_digest=context.scorer_lock.digest, published_results_digest=context.published.digest,
                            limits=context.limits, mode="smoke" if mode == "smoke" else "reproduction",
                            smoke_max_new_tokens=64 if mode == "smoke" else None)
    context.plan_path.write_text(json.dumps(plan.to_dict()))
    context.dataset_verifier.return_value = datasets
    assert entrypoint.main(arguments(context, "run-reproduction")) == 2
    assert "full reproduction" in capsys.readouterr().err
    context.reproduction_runner.assert_not_called()


@pytest.mark.parametrize("option", ["--runtime", "--scorer-lock", "--published-results", "--lock"])
def test_full_run_dry_rejects_missing_locked_input(
    entrypoint: ModuleType, context: SimpleNamespace, capsys: pytest.CaptureFixture[str], option: str,
) -> None:
    assert entrypoint.main([option, str(context.root / "missing.json"), *arguments(context, "run-reproduction")]) == 2
    assert json.loads(capsys.readouterr().err)["ok"] is False
    context.scorer_loader.assert_not_called()
    context.reproduction_runner.assert_not_called()


def test_changed_evaluator_lock_cannot_enter_frozen_full_run(
    entrypoint: ModuleType, context: SimpleNamespace, capsys: pytest.CaptureFixture[str],
) -> None:
    context.dataset_loader.return_value.digest = "f" * 64
    assert entrypoint.main(arguments(context, "run-reproduction")) == 2
    assert "locks differ" in capsys.readouterr().err
    context.reproduction_runner.assert_not_called()


@pytest.mark.parametrize("ready", [False, True])
def test_readonly_gpu_check_has_machine_readable_exit_status_and_does_not_load_evaluator_assets(
    entrypoint: ModuleType, context: SimpleNamespace, capsys: pytest.CaptureFixture[str], ready: bool,
) -> None:
    context.readiness.return_value = {"ready_for_acceptance": ready, "model_execution_performed": False}
    assert entrypoint.main(["check-gpu", "--gpu-device", "0", "--output-parent", str(context.root),
                            "--required-free-bytes", "1234"]) == (0 if ready else 1)
    result = json.loads(capsys.readouterr().out)
    assert result["ok"] is ready and result["executed"] is False
    assert context.readiness.call_args.kwargs["required_free_bytes"] == 1234
    context.dataset_loader.assert_not_called()
    context.scorer_loader.assert_not_called()
    context.acceptance_runner.assert_not_called()
    context.reproduction_runner.assert_not_called()
