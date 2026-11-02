from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock

import pytest
from test_weight_cohort import EXAMPLE, replay_context

from autocontext.weight_evolution import evaluation, patch_worker, replay, replay_commands, report


@pytest.fixture
def example_entrypoint() -> ModuleType:
    spec = importlib.util.spec_from_file_location("weight_replay_example", EXAMPLE / "run.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def command_context(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    context = replay_context(count=2, mode="smoke")
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(context.plan.to_dict()))
    runs = tmp_path / "results" / "runs"
    runs.mkdir(parents=True)
    for shard in context.plan.shards:
        (runs / shard.shard_id).mkdir()
    dataset_loader = Mock(return_value=SimpleNamespace(digest=context.plan.dataset_lock_digest))
    scorer_loader = Mock(return_value=SimpleNamespace(digest=context.plan.scorer_lock_digest))
    published_loader = Mock(return_value=SimpleNamespace(digest=context.plan.published_results_digest))
    dataset_verifier = Mock(return_value=(context.dataset,))
    monkeypatch.setattr(replay_commands, "load_dataset_lock", dataset_loader)
    monkeypatch.setattr(replay_commands, "load_scorer_lock", scorer_loader)
    monkeypatch.setattr(replay_commands, "load_published_results", published_loader)
    monkeypatch.setattr(replay_commands, "verify_datasets", dataset_verifier)
    receipts = {receipt.variant: receipt for receipt in context.receipts}
    checkpoint_verifier = Mock(side_effect=lambda lock, root, directory, expected_variant: receipts[expected_variant])
    monkeypatch.setattr(patch_worker, "verify_checkpoint", checkpoint_verifier)
    execute = Mock(return_value={"ok": True, "dispatched_this_invocation": len(context.plan.shards)})
    monkeypatch.setattr(replay, "run_replay", execute)
    return SimpleNamespace(context=context, root=tmp_path, plan_path=plan_path, dataset_loader=dataset_loader,
                           scorer_loader=scorer_loader, published_loader=published_loader,
                           dataset_verifier=dataset_verifier, checkpoint_verifier=checkpoint_verifier, execute=execute)


def run_args(context: SimpleNamespace) -> list[str]:
    return ["run-replay", "--plan", str(context.plan_path), "--asset-root", str(context.root / "assets"),
            "--dataset-root", str(context.root / "datasets"), "--base-checkpoint", str(context.root / "base"),
            "--patch-checkpoint", str(context.root / "patch"), "--output", str(context.root / "results"),
            "--gpu-device", "0"]


@pytest.mark.parametrize("command", ["fetch-datasets", "verify-datasets"])
def test_dataset_commands_route_explicit_fetch_or_offline_verify_without_scorer_loading(
    example_entrypoint: ModuleType, command_context: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str], command: str,
) -> None:
    fetch = Mock(return_value=(command_context.context.dataset,))
    forbidden = Mock(side_effect=AssertionError("scorer loading is not metadata verification"))
    monkeypatch.setattr(replay_commands, "fetch_datasets", fetch)
    monkeypatch.setattr(replay_commands, "load_pinned_scorer", forbidden)
    args = ["--datasets-lock", str(command_context.root / "selected.lock.json"), command,
            "--dataset-root", str(command_context.root / "datasets"), "--dataset", "math500"]
    if command == "fetch-datasets":
        args += ["--max-download-bytes", "12345"]
    assert example_entrypoint.main(args) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["datasets"][0]["dataset"] == "math500"
    assert '"answer"' not in json.dumps(payload)
    command_context.dataset_loader.assert_called_with(command_context.root / "selected.lock.json")
    if command == "fetch-datasets":
        assert fetch.call_args.kwargs == {"dataset_names": ["math500"], "max_download_bytes": 12345}
        command_context.dataset_verifier.assert_not_called()
    else:
        fetch.assert_not_called()
        command_context.dataset_verifier.assert_called_once()
    forbidden.assert_not_called()


@pytest.mark.parametrize("command", ["fetch-scorers", "verify-scorers"])
def test_scorer_metadata_operations_do_not_import_or_run_checkers(
    example_entrypoint: ModuleType, command_context: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str], command: str,
) -> None:
    fetch, verify = Mock(return_value={"ok": True}), Mock(return_value={"ok": True})
    forbidden = Mock(side_effect=AssertionError("must remain data-only"))
    monkeypatch.setattr(replay_commands, "fetch_scorer_assets", fetch)
    monkeypatch.setattr(replay_commands, "verify_scorer_assets", verify)
    monkeypatch.setattr(replay_commands, "load_pinned_scorer", forbidden)
    assert example_entrypoint.main([command, "--scorer-root", str(command_context.root)]) == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True
    assert (fetch.call_count, verify.call_count) == ((1, 0) if command == "fetch-scorers" else (0, 1))
    forbidden.assert_not_called()


def test_preflight_is_explicit_checker_loading_and_complete_cohort_validation(
    example_entrypoint: ModuleType, command_context: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    scorer = Mock()
    scorer.provenance.return_value = {"execution_kind": "fixture"}
    loader = Mock(return_value=scorer)
    monkeypatch.setattr(replay_commands, "load_pinned_scorer", loader)
    assert example_entrypoint.main(["preflight-scorers", "--scorer-root", str(command_context.root),
                                    "--dataset-root", str(command_context.root)]) == 0
    scorer.validate_cohort.assert_called_once_with("math500", command_context.context.dataset.problems)
    assert json.loads(capsys.readouterr().out)["ok"] is True


def test_plan_command_requires_finite_budgets_and_never_overwrites_existing_plan(
    example_entrypoint: ModuleType, command_context: SimpleNamespace, capsys: pytest.CaptureFixture[str],
) -> None:
    output = command_context.root / "new-plan.json"
    args = ["plan-replay", "--asset-root", str(command_context.root), "--dataset-root", str(command_context.root),
            "--base-checkpoint", str(command_context.root / "base"), "--patch-checkpoint", str(command_context.root / "patch"),
            "--output", str(output), "--max-total-generated-tokens", "1000000000", "--max-total-wall-seconds", "10000000"]
    assert example_entrypoint.main(args) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["model_execution_performed"] is False
    original = output.read_bytes()
    assert example_entrypoint.main(args) == 2
    assert json.loads(capsys.readouterr().err)["ok"] is False
    assert output.read_bytes() == original
    command_context.execute.assert_not_called()
    args[-1] = "nan"
    assert example_entrypoint.main(args) == 2
    assert output.read_bytes() == original


def test_run_replay_defaults_to_resource_plan_without_gpu_dispatch(
    example_entrypoint: ModuleType, command_context: SimpleNamespace, capsys: pytest.CaptureFixture[str],
) -> None:
    assert example_entrypoint.main(run_args(command_context)) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["executed"] is False and payload["model_execution_performed"] is False
    assert payload["plan_digest"] == command_context.context.plan.digest
    command_context.execute.assert_not_called()
    command_context.checkpoint_verifier.assert_not_called()


def test_execute_is_explicit_and_prints_bound_resource_plan_before_dispatch(
    example_entrypoint: ModuleType, command_context: SimpleNamespace, capsys: pytest.CaptureFixture[str],
) -> None:
    assert example_entrypoint.main([*run_args(command_context), "--execute"]) == 0
    streams = capsys.readouterr()
    assert json.loads(streams.err)["plan_digest"] == command_context.context.plan.digest
    assert json.loads(streams.out)["dispatched_this_invocation"] == len(command_context.context.plan.shards)
    assert command_context.execute.call_args.args[0] == command_context.context.plan


@pytest.mark.parametrize("field", ["dataset_loader", "scorer_loader", "published_loader"])
def test_changed_lock_identity_rejects_even_dry_run(
    example_entrypoint: ModuleType, command_context: SimpleNamespace, capsys: pytest.CaptureFixture[str], field: str,
) -> None:
    getattr(command_context, field).return_value = SimpleNamespace(digest="f" * 64)
    assert example_entrypoint.main(run_args(command_context)) == 2
    assert "locks differ" in json.loads(capsys.readouterr().err)["error"]
    command_context.execute.assert_not_called()


@pytest.mark.parametrize("allow_fixture", [False, True])
def test_reporting_requires_explicit_fixture_opt_in(
    example_entrypoint: ModuleType, command_context: SimpleNamespace, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str], allow_fixture: bool,
) -> None:
    scorer, evaluated = object(), object()
    monkeypatch.setattr(replay_commands, "load_pinned_scorer", Mock(return_value=scorer))
    evaluator = Mock(return_value=evaluated)
    monkeypatch.setattr(evaluation, "evaluate_reproduction", evaluator)
    writer = Mock(return_value={"report_path": "report.json"})
    monkeypatch.setattr(report, "write_reproduction_report", writer)
    args = ["report-replay", "--plan", str(command_context.plan_path), "--dataset-root", str(command_context.root),
            "--scorer-root", str(command_context.root), "--runs-root", str(command_context.root / "results"),
            "--output", str(command_context.root / "report")]
    if allow_fixture:
        args.append("--allow-fixture")
    assert example_entrypoint.main(args) == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True
    assert evaluator.call_args.kwargs["allow_fixture"] is allow_fixture
    assert evaluator.call_args.args[2] is scorer
    writer.assert_called_once_with(evaluated, command_context.root / "report")


def test_command_errors_are_structured_without_success_output(
    example_entrypoint: ModuleType, command_context: SimpleNamespace, capsys: pytest.CaptureFixture[str],
) -> None:
    command_context.dataset_verifier.side_effect = ValueError("dataset SHA-256 mismatch")
    assert example_entrypoint.main(["verify-datasets", "--dataset-root", str(command_context.root)]) == 2
    streams = capsys.readouterr()
    assert streams.out == ""
    assert json.loads(streams.err) == {"ok": False, "stage": "verify-datasets", "error": "dataset SHA-256 mismatch"}
