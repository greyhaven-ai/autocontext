from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import socket
import subprocess
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from autocontext.weight_evolution.adaptive_provider import OfflineFeedbackProvider, ProviderSessionAdapter
from autocontext.weight_evolution.adaptive_workflow import (
    load_adaptive_workflow,
    run_adaptive_workflow,
    validate_adaptive_workflow,
)
from autocontext.weight_evolution.comparison_io import load_comparison_fixture

EXAMPLE = Path(__file__).resolve().parents[2] / "examples" / "weight_evolution" / "qwen_adaptive"
COMPARISON = EXAMPLE.parent / "qwen_comparison" / "fixtures"


def builder() -> ModuleType:
    spec = importlib.util.spec_from_file_location("adaptive_example_builder", EXAMPLE / "build_fixture.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run(output: Path, fixtures: Path = EXAMPLE / "fixtures") -> dict[str, Any]:
    return run_adaptive_workflow(fixtures, comparison_directory=COMPARISON, output_dir=output)


def record(path: Path) -> dict[str, Any]:
    return json.loads(path.read_bytes())["payload"]  # type: ignore[no-any-return]


def files(path: Path) -> dict[str, bytes]:
    return {str(p.relative_to(path)): p.read_bytes() for p in path.rglob("*") if p.is_file()}


@pytest.fixture
def bundle(tmp_path: Path) -> Path:
    return Path(shutil.copytree(EXAMPLE / "fixtures", tmp_path / "fixtures"))


def repin(bundle: Path, name: str, value: bytes) -> None:
    (bundle / f"{name}.json").write_bytes(value)
    lock = json.loads((bundle / "fixtures.lock.json").read_bytes())
    lock[f"{name}_sha256"], lock[f"{name}_size_bytes"] = hashlib.sha256(value).hexdigest(), len(value)
    (bundle / "fixtures.lock.json").write_text(json.dumps(lock))


def test_adaptive_selection_restores_earlier_winner_and_confirmation_veto_blocks_audit(tmp_path: Path) -> None:
    output = tmp_path / "run"
    result = run(output)
    assert result["model_execution_performed"] is result["promotion_eligible"] is result["benchmark_result_claimed"] is False
    assert result["gpu_execution_deferred"] is True
    assert result["audit_status"] == "withheld_after_confirmation_veto"
    assert result["audit_report_digest"] is None
    assert [row["status"] for row in result["confirmation_outcomes"]] == ["confirmed", "confirmed", "vetoed"]
    assert not (output / "audit").exists() and not (output / "audit-plan.json").exists()
    for mechanism, calls, restored in (("C", 12, True), ("W", 9, False), ("D", 12, True)):
        selection = record(output / "search" / mechanism / "selection.json")
        complete = record(output / "search" / mechanism / "complete.json")
        assert selection["baseline_practice_scores"] == [0, 0, 1]
        assert selection["practice_scores"] == [1, 1, 1] and selection["selected_round"] == 2
        assert complete["selected_round"] == 2 and complete["restoration_performed"] is restored
        assert complete["reserved_usage"]["operations"] == complete["artifact"]["source"]["declared_search_calls"] == calls
        assert complete["confirmation_used"] is complete["final_audit_used"] is False
        if restored:
            assert complete["candidate_digest"] != complete["selected_candidate_digest"]
        failures = tuple((output / "search" / mechanism / "session" / "failures").iterdir())
        assert len(failures) == (0 if mechanism == "C" else 1)


def test_all_confirmed_searches_allow_only_the_exact_pinned_audit(tmp_path: Path) -> None:
    fixtures = tmp_path / "pass-fixtures"
    builder().build_fixture(fixtures, confirmation_outcome="pass")
    output = tmp_path / "run"
    result = run(output, fixtures)
    assert result["audit_status"] == "completed_fixture_only"
    assert all(row["status"] == "confirmed" for row in result["confirmation_outcomes"])
    source, _ = load_comparison_fixture(COMPARISON)
    plan = json.loads((output / "audit-plan.json").read_bytes())
    assert plan["cells"] == [cell.to_dict() for cell in source.cells]
    assert len(plan["cells"]) == 60
    report = json.loads((output / "audit" / "report.json").read_bytes())
    assert report["benchmark_result_claimed"] is report["promotion_claimed"] is False
    assert run(output, fixtures) == result


def test_all_searches_are_sealed_before_confirmation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from autocontext.weight_evolution import adaptive_workflow

    output = tmp_path / "run"
    original = adaptive_workflow.confirm_adaptive_selection
    calls = []

    def check(*args: Any, **kwargs: Any) -> Any:
        assert (output / "selection.json").is_file()
        for mechanism in ("C", "W", "D"):
            assert record(output / "search" / mechanism / "complete.json")["status"] == "sealed"
            assert record(output / "search" / mechanism / "session" / "export.json")["status"] == "sealed"
        calls.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(adaptive_workflow, "confirm_adaptive_selection", check)
    run(output)
    assert calls == [1]


def test_provider_requests_contain_only_practice_state_and_feedback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    prompts = []
    original = OfflineFeedbackProvider.complete

    def capture(self: OfflineFeedbackProvider, *args: Any, **kwargs: Any) -> Any:
        prompts.append(kwargs["user_prompt"])
        return original(self, *args, **kwargs)

    monkeypatch.setattr(OfflineFeedbackProvider, "complete", capture)
    run(tmp_path / "run")
    assert len(prompts) == 9 and len(set(prompts)) == 9
    for prompt in prompts:
        for hidden in ("confirmation.", "audit.", '"answer":', '"instructions":', "What is eight plus six?",
                       "What is 17 plus 25?", "coral"):
            assert hidden not in prompt
        assert "practice.sum" in prompt and "response_sha256" in prompt and '"score":' in prompt


def test_resume_uses_identical_files_without_provider_or_probe_dispatch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    output = tmp_path / "run"
    first = run(output)
    before = files(output)

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("resume dispatched search work")

    monkeypatch.setattr(OfflineFeedbackProvider, "complete", forbidden)
    monkeypatch.setattr(ProviderSessionAdapter, "execute", forbidden)
    assert run(output) == first
    assert files(output) == before


def test_independent_runs_have_identical_durable_evidence(tmp_path: Path) -> None:
    a, b = tmp_path / "a", tmp_path / "b"
    assert run(a) == run(b)
    assert files(a) == files(b)


def test_no_network_or_process_execution(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("offline adaptive search attempted external execution")

    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    run(tmp_path / "run")


def test_validate_and_builder_are_offline_and_reproduce_pins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("preflight dispatched")

    monkeypatch.setattr(ProviderSessionAdapter, "execute", forbidden)
    result = validate_adaptive_workflow(EXAMPLE / "fixtures", COMPARISON)
    assert result["search_policy"]["proposal_rounds"] == 3
    target = tmp_path / "rebuilt"
    builder().build_fixture(target)
    assert files(target) == files(EXAMPLE / "fixtures")
    with pytest.raises(ValueError, match="new directory"):
        builder().build_fixture(target)


@pytest.mark.parametrize("name", ["data", "workflow", "confirmation"])
def test_tampered_bytes_rejected(bundle: Path, name: str) -> None:
    path = bundle / f"{name}.json"
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(ValueError, match="size/hash"):
        load_adaptive_workflow(bundle)


@pytest.mark.parametrize("name", ["data", "workflow", "confirmation"])
def test_duplicate_keys_rejected_after_repin(bundle: Path, name: str) -> None:
    value = (bundle / f"{name}.json").read_bytes()
    repin(bundle, name, b'{"schema_version":1,' + value[1:])
    with pytest.raises(ValueError, match="duplicate JSON key"):
        load_adaptive_workflow(bundle)


def test_equal_budgets_and_worst_case_capacity_enforced_before_output(bundle: Path, tmp_path: Path) -> None:
    workflow = json.loads((bundle / "workflow.json").read_bytes())
    workflow["arms"][1]["spec"]["limits"]["max_operations"] = 11
    repin(bundle, "workflow", json.dumps(workflow).encode())
    with pytest.raises(ValueError, match="equal limits|worst-case"):
        run(tmp_path / "run", bundle)
    assert not (tmp_path / "run").exists()


def test_veto_evidence_cannot_be_replaced_with_passing_evidence_on_resume(tmp_path: Path) -> None:
    output = tmp_path / "run"
    result = run(output)
    before = files(output)
    passing = tmp_path / "passing"
    builder().build_fixture(passing, confirmation_outcome="pass")
    with pytest.raises(ValueError, match="immutable workflow artifact changed"):
        run(output, passing)
    assert files(output) == before and result["audit_report_digest"] is None


@pytest.mark.parametrize("filename", ["workflow.json", "selection.json", "complete.json"])
def test_top_level_evidence_tampering_fails(tmp_path: Path, filename: str) -> None:
    output = tmp_path / "run"
    run(output)
    (output / filename).write_text("{}")
    with pytest.raises(ValueError, match="immutable workflow artifact changed"):
        run(output)


@pytest.mark.parametrize("artifact,symlink", [("audit", False), ("audit", True), ("audit-plan.json", True)])
def test_unexpected_audit_after_veto_is_rejected(tmp_path: Path, artifact: str, symlink: bool) -> None:
    output = tmp_path / "run"
    run(output)
    if symlink:
        (output / artifact).symlink_to(output / "missing-target")
    else:
        (output / artifact).mkdir()
    with pytest.raises(ValueError, match="audit artifacts cannot exist"):
        run(output)


def test_symlink_and_unowned_output_rejected(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "link"
    link.symlink_to(target, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        run(link)
    (target / "user-file").write_text("keep")
    with pytest.raises(ValueError, match="unowned"):
        run(target)
    assert (target / "user-file").read_text() == "keep"
