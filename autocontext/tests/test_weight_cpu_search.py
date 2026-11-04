from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from autocontext.weight_evolution import cpu_search
from autocontext.weight_evolution.artifacts import canonical_digest
from autocontext.weight_evolution.checkpoint_candidates import TensorCandidateRegistry
from autocontext.weight_evolution.cpu_search import load_cpu_search, run_cpu_search, validate_cpu_search
from autocontext.weight_evolution.session import _read_record

EXAMPLE = Path(__file__).resolve().parents[2] / "examples" / "weight_evolution" / "cpu_adaptive"
pytestmark = pytest.mark.skipif(sys.platform not in {"linux", "darwin"}, reason="CPU search requires Linux or macOS limits")


def builder() -> ModuleType:
    spec = importlib.util.spec_from_file_location("cpu_adaptive_bundle_builder", EXAMPLE / "build_bundle.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def files(root: Path) -> dict[str, bytes]:
    return {str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file()}


def forbid(*args: Any, **kwargs: Any) -> Any:
    raise AssertionError("retained CPU search dispatched new work")


def repin(directory: Path, value: dict[str, Any]) -> None:
    payload = json.dumps(value).encode()
    (directory / "study.json").write_bytes(payload)
    lock = json.loads((directory / "bundle.lock.json").read_bytes())
    lock["spec_sha256"], lock["spec_size_bytes"] = hashlib.sha256(payload).hexdigest(), len(payload)
    (directory / "bundle.lock.json").write_text(json.dumps(lock))


def event(output: Path, label: str) -> tuple[Path, Path, dict[str, Any]]:
    for path in sorted((output / "claims").iterdir()):
        claim = _read_record(path)
        if claim["label"] == label:
            return path, output / "results" / path.name, claim
    raise AssertionError(f"missing event {label}")


def rehash(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps({"payload": payload, "digest": canonical_digest(payload)}))


@pytest.fixture(scope="module")
def completed(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path, dict[str, Any]]:
    root = tmp_path_factory.mktemp("cpu-search")
    bundle, output = root / "bundle", root / "output"
    builder().build_bundle(bundle)
    return bundle, output, run_cpu_search(bundle, output_dir=output)


@pytest.fixture
def retained(completed: tuple[Path, Path, dict[str, Any]], tmp_path: Path) -> tuple[Path, Path, dict[str, Any]]:
    bundle, output, result = completed
    return (Path(shutil.copytree(bundle, tmp_path / "bundle")),
            Path(shutil.copytree(output, tmp_path / "output")), result)


def test_real_feedback_search_restores_earlier_winners(completed: tuple[Path, Path, dict[str, Any]]) -> None:
    _, output, result = completed
    assert result["model_execution_performed"] is True and result["gpu_execution_deferred"] is True
    assert result["promotion_eligible"] is result["benchmark_result_claimed"] is False
    assert result["selected_options"] == {"C": "c-hint-1", "W": "w-row-two-1", "D": "d-temperature-1"}
    assert result["restoration_verified"] == {"C": True, "W": True, "D": True}
    assert result["audit_status"] == "completed"
    assert result["reserved_usage"]["proposals"] == 9
    assert result["reserved_usage"]["materializations"] == 5
    assert result["reserved_usage"]["inference_calls"] == result["reserved_usage"]["output_tokens"] == 130
    expected_trials = {"C": [1, 3, 2, 3], "W": [1, 2, 2, 0], "D": [1, 5, 4, 1]}
    assert {key: [row["correct"] for row in history] for key, history in result["histories"].items()} == expected_trials
    for mechanism in ("C", "W", "D"):
        _, trial_path, _ = event(output, f"{mechanism}-trial-1")
        _, restored_path, _ = event(output, f"{mechanism}-restored")
        trial = _read_record(trial_path)["value"]["results"]
        restored = _read_record(restored_path)["value"]["results"]
        assert [(row["text"], row["logits"]) for row in trial] == [(row["text"], row["logits"]) for row in restored]
    for role in ("practice", "confirmation", "audit"):
        report = _read_record(output / "reports" / f"{role}.json")
        expected = {"A": 0.2, "C": 0.6, "W": 0.4, "CW": 0.8, "D": 1.0 if role == "practice" else 0.8}
        assert {key: value["accuracy"] for key, value in report["conditions"].items()} == expected


def test_measured_practice_feedback_changes_next_weight_proposal(
    completed: tuple[Path, Path, dict[str, Any]], tmp_path: Path,
) -> None:
    bundle, _, original = completed
    changed = Path(shutil.copytree(bundle, tmp_path / "bundle"))
    spec = json.loads((changed / "study.json").read_bytes())
    # The same first edit now loses a correct practice answer. No option or
    # proposal order is changed; the measured score must alter the next choice.
    next(case for case in spec["cases"] if case["case_id"] == "practice-3")["expected"] = "one"
    repin(changed, spec)
    result = run_cpu_search(changed, output_dir=tmp_path / "output")
    original_ids = [row["option_id"] for row in original["histories"]["W"]]
    changed_ids = [row["option_id"] for row in result["histories"]["W"]]
    assert original_ids[:3] == [None, "w-row-two-1", "w-row-two-2"]
    assert changed_ids[:3] == [None, "w-row-two-1", "w-row-zero-1"]
    assert result["selected_options"]["W"] is None
    assert result["audit_status"] == "withheld"


def test_workers_and_policy_receive_no_heldout_or_scorer_authority(
    completed: tuple[Path, Path, dict[str, Any]],
) -> None:
    _, output, _ = completed
    proposal_count = 0
    for path in sorted((output / "claims").iterdir()):
        claim = _read_record(path)
        if claim["kind"] == "proposal":
            proposal_count += 1
            encoded = json.dumps(claim["input"])
            assert not any(word in encoded for word in ("confirmation-", "audit-", '"expected"', '"scorer"'))
        elif claim["kind"] == "inference":
            # Request schemas themselves exclude gold labels and scorer fields.
            _, result_path, _ = event(output, claim["label"])
            receipt = _read_record(result_path)["value"]
            assert receipt["device"] == "cpu" and receipt["gpu_execution_performed"] is False
            encoded = json.dumps(claim["input"])
            assert not any(f'"{key}"' in encoded for key in ("expected", "gold", "scorer", "responses"))
    assert proposal_count == 9


def test_search_costs_are_matched_charged_and_seed_is_frozen(completed: tuple[Path, Path, dict[str, Any]]) -> None:
    _, output, result = completed
    claims = [_read_record(path) for path in sorted((output / "claims").iterdir())]
    totals = {meter: sum(claim["reserved"][meter] for claim in claims) for meter in result["reserved_usage"]}
    assert totals == result["reserved_usage"]
    capacity = {"proposals": 3, "materializations": 0, "inference_calls": 25,
                "input_tokens": 75, "output_tokens": 25}
    assert result["search_capacity_per_mechanism"] == capacity
    for mechanism in ("C", "W", "D"):
        arm = [claim for claim in claims if claim["label"].startswith(f"{mechanism}-")]
        actual = {meter: sum(claim["reserved"][meter] for claim in arm) for meter in totals}
        assert result["search_usage"][mechanism] == actual
        assert actual["proposals"] == 3 and actual["inference_calls"] == 25
        assert all(actual[meter] <= capacity[meter] for meter in actual)
        decisions = []
        for claim in arm:
            if claim["kind"] == "inference":
                assert {request["decoding"]["seed"] for request in claim["input"]["requests"]} == {406}
            elif claim["kind"] == "proposal":
                _, path, _ = event(output, claim["label"])
                decisions.append(_read_record(path)["value"])
        assert result["proposal_history"][mechanism] == decisions
    assert sum(claim["kind"] == "proposal" for claim in claims) == 9
    assert not any(claim["kind"] == "proposal" and "CW" in claim["label"] for claim in claims)


def test_exact_resume_does_no_inference_proposals_or_materialization(
    retained: tuple[Path, Path, dict[str, Any]], monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle, output, result = retained
    before = files(output)
    monkeypatch.setattr(cpu_search, "run_cpu_batch", forbid)
    monkeypatch.setattr(TensorCandidateRegistry, "materialize", forbid)
    assert run_cpu_search(bundle, output_dir=output) == result
    assert files(output) == before


def test_fresh_runs_share_semantic_evidence(completed: tuple[Path, Path, dict[str, Any]], tmp_path: Path) -> None:
    bundle, output, result = completed
    fresh = tmp_path / "fresh"
    repeated = run_cpu_search(bundle, output_dir=fresh)
    assert repeated["semantic_digest"] == result["semantic_digest"]
    assert repeated["receipt_digests"] == result["receipt_digests"]
    assert files(output / "reports") == files(fresh / "reports")
    assert repeated["receipt_evidence_digests"] != result["receipt_evidence_digests"]


def test_veto_withholds_audit_without_reselection_and_replays(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bundle, output = tmp_path / "bundle", tmp_path / "output"
    builder().build_bundle(bundle, confirmation_outcome="veto")
    result = run_cpu_search(bundle, output_dir=output)
    assert result["confirmation"]["outcomes"] == {"C": True, "W": True, "D": False}
    assert result["audit_status"] == "withheld"
    assert result["selected_options"] == {"C": "c-hint-1", "W": "w-row-two-1", "D": "d-temperature-1"}
    assert result["reserved_usage"]["inference_calls"] == 105
    assert not (output / "reports" / "audit.json").exists()
    assert not any(_read_record(path)["label"].startswith("audit-") for path in (output / "claims").iterdir())
    before = files(output)
    monkeypatch.setattr(cpu_search, "run_cpu_batch", forbid)
    assert run_cpu_search(bundle, output_dir=output) == result
    assert files(output) == before


def test_validate_is_readonly_and_bundle_reproducible(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    first, second = tmp_path / "first", tmp_path / "second"
    builder().build_bundle(first)
    builder().build_bundle(second)
    assert files(first) == files(second)
    before = files(first)
    monkeypatch.setattr(cpu_search, "run_cpu_batch", forbid)
    monkeypatch.setattr(TensorCandidateRegistry, "materialize", forbid)
    result = validate_cpu_search(first)
    assert result["model_execution_performed"] is False
    assert result["required_usage"]["inference_calls"] == 130
    assert files(first) == before
    with pytest.raises(ValueError, match="fresh directory"):
        builder().build_bundle(first)


def test_checked_in_bundle_rebuilds_exactly(tmp_path: Path) -> None:
    rebuilt = tmp_path / "bundle"
    builder().build_bundle(rebuilt)
    assert files(rebuilt) == files(EXAMPLE / "bundle")
    assert validate_cpu_search(EXAMPLE / "bundle")["required_usage"]["inference_calls"] == 130


@pytest.mark.parametrize("field", ["max_proposals", "max_materializations", "max_inference_calls",
                                    "max_input_tokens", "max_output_tokens"])
def test_underbudget_refused_before_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, field: str) -> None:
    bundle, output = tmp_path / "bundle", tmp_path / "output"
    builder().build_bundle(bundle)
    spec = json.loads((bundle / "study.json").read_bytes())
    spec["budget"][field] = 1
    repin(bundle, spec)
    monkeypatch.setattr(cpu_search, "run_cpu_batch", forbid)
    with pytest.raises(ValueError, match="budget"):
        run_cpu_search(bundle, output_dir=output)
    assert not output.exists()


@pytest.mark.parametrize("failure", [ValueError, KeyboardInterrupt])
@pytest.mark.parametrize("operation,label,meter,amount", [
    ("materialize", "materialize-base", "materializations", 1),
    ("proposal", "C-proposal-1", "proposals", 1),
    ("inference", "C-baseline", "inference_calls", 5),
])
def test_failed_or_ambiguous_work_remains_charged_without_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: type[BaseException],
    operation: str, label: str, meter: str, amount: int,
) -> None:
    bundle, output = tmp_path / "bundle", tmp_path / "output"
    builder().build_bundle(bundle)

    def interrupted(*args: Any, **kwargs: Any) -> Any:
        raise failure("worker interrupted")

    if operation == "materialize":
        monkeypatch.setattr(TensorCandidateRegistry, "materialize", interrupted)
    elif operation == "proposal":
        monkeypatch.setattr(cpu_search, "choose_policy_option", interrupted)
    else:
        monkeypatch.setattr(cpu_search, "run_cpu_batch", interrupted)
    expected = KeyboardInterrupt if failure is KeyboardInterrupt else cpu_search.CpuSearchBlockedError
    message = "worker interrupted" if failure is KeyboardInterrupt else f"failed {label} remains charged"
    with pytest.raises(expected, match=message):
        run_cpu_search(bundle, output_dir=output)
    _, _, claim = event(output, label)
    assert claim["reserved"][meter] == amount
    before = files(output)
    monkeypatch.setattr(cpu_search, "run_cpu_batch", forbid)
    monkeypatch.setattr(TensorCandidateRegistry, "materialize", forbid)
    with pytest.raises(ValueError):
        run_cpu_search(bundle, output_dir=output)
    assert files(output) == before


@pytest.mark.parametrize("missing", ["selection.json", "seals/row-two-1.json", "confirmation.json", "reports/audit.json"])
def test_missing_derived_evidence_cannot_be_repaired(
    retained: tuple[Path, Path, dict[str, Any]], monkeypatch: pytest.MonkeyPatch, missing: str,
) -> None:
    bundle, output, _ = retained
    target = output / missing
    target.unlink()
    monkeypatch.setattr(cpu_search, "run_cpu_batch", forbid)
    monkeypatch.setattr(TensorCandidateRegistry, "materialize", forbid)
    with pytest.raises(ValueError):
        run_cpu_search(bundle, output_dir=output)
    assert not target.exists()


@pytest.mark.parametrize("which", ["claim", "result", "both"])
def test_journal_gap_rejected_before_dispatch(
    retained: tuple[Path, Path, dict[str, Any]], monkeypatch: pytest.MonkeyPatch, which: str,
) -> None:
    bundle, output, _ = retained
    claim, result, _ = event(output, "C-trial-1")
    if which in {"claim", "both"}:
        claim.unlink()
    if which in {"result", "both"}:
        result.unlink()
    (output / "complete.json").unlink()
    monkeypatch.setattr(cpu_search, "run_cpu_batch", forbid)
    with pytest.raises(ValueError):
        run_cpu_search(bundle, output_dir=output)


@pytest.mark.parametrize("candidate_id", ["row-two-1", "row-zero-1", "row-zero-2"])
def test_materialized_checkpoint_integrity_required_on_resume(
    retained: tuple[Path, Path, dict[str, Any]], monkeypatch: pytest.MonkeyPatch, candidate_id: str,
) -> None:
    bundle, output, _ = retained
    candidate = next(c for c in load_cpu_search(bundle).candidates if c.candidate_id == candidate_id)
    checkpoint = output / "materializations" / candidate.digest / "model.safetensors"
    checkpoint.write_bytes(checkpoint.read_bytes() + b"tamper")
    monkeypatch.setattr(cpu_search, "run_cpu_batch", forbid)
    monkeypatch.setattr(TensorCandidateRegistry, "materialize", forbid)
    with pytest.raises(ValueError):
        run_cpu_search(bundle, output_dir=output)


@pytest.mark.parametrize("mutation", ["runtime", "text", "telemetry", "integer_flag"])
@pytest.mark.parametrize("label", ["C-baseline", "audit-D"])
def test_rehashed_inference_tampering_rejected(
    retained: tuple[Path, Path, dict[str, Any]], monkeypatch: pytest.MonkeyPatch, mutation: str, label: str,
) -> None:
    bundle, output, _ = retained
    _, path, _ = event(output, label)
    payload = _read_record(path)
    receipt = payload["value"]
    if mutation == "runtime":
        receipt["runtime_digest"] = "0" * 64
    elif mutation == "text":
        receipt["results"][0]["text"] = "one"
    elif mutation == "telemetry":
        receipt["telemetry"]["elapsed_seconds"] += 0.01
    else:
        receipt["model_execution_performed"] = 1
    rehash(path, payload)
    monkeypatch.setattr(cpu_search, "run_cpu_batch", forbid)
    with pytest.raises(ValueError):
        run_cpu_search(bundle, output_dir=output)


@pytest.mark.parametrize("directory", ["claims", "results", "reports", "seals"])
def test_unowned_evidence_rejected_before_dispatch(
    retained: tuple[Path, Path, dict[str, Any]], monkeypatch: pytest.MonkeyPatch, directory: str,
) -> None:
    bundle, output, _ = retained
    (output / "complete.json").unlink()
    (output / directory / "unknown.json").write_text("{}")
    monkeypatch.setattr(cpu_search, "run_cpu_batch", forbid)
    with pytest.raises(ValueError):
        run_cpu_search(bundle, output_dir=output)


@pytest.mark.parametrize("artifact", ["confirmation", "selection"])
def test_confirmation_and_selection_tampering_rejected(
    retained: tuple[Path, Path, dict[str, Any]], monkeypatch: pytest.MonkeyPatch, artifact: str,
) -> None:
    bundle, output, _ = retained
    path = output / f"{artifact}.json"
    payload = _read_record(path)
    if artifact == "confirmation":
        payload["status"] = "vetoed"
    else:
        payload["selections"]["W"]["option_id"] = "w-row-zero-1"
    rehash(path, payload)
    monkeypatch.setattr(cpu_search, "run_cpu_batch", forbid)
    with pytest.raises(ValueError):
        run_cpu_search(bundle, output_dir=output)
