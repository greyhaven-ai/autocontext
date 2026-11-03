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

from autocontext.weight_evolution import cpu_study
from autocontext.weight_evolution.artifacts import canonical_digest
from autocontext.weight_evolution.checkpoint_candidates import TensorCandidateRegistry
from autocontext.weight_evolution.cpu_study import load_cpu_study, run_cpu_study, validate_cpu_study
from autocontext.weight_evolution.session import _read_record

EXAMPLE = Path(__file__).resolve().parents[2] / "examples" / "weight_evolution" / "cpu_inference"
pytestmark = pytest.mark.skipif(sys.platform not in {"linux", "darwin"}, reason="CPU study requires Linux or macOS limits")


def builder() -> ModuleType:
    spec = importlib.util.spec_from_file_location("cpu_bundle_builder", EXAMPLE / "build_bundle.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def files(root: Path) -> dict[str, bytes]:
    return {str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file()}


def forbid(*args: Any, **kwargs: Any) -> Any:
    raise AssertionError("retained CPU study dispatched new work")


def repin(directory: Path, spec: dict[str, Any]) -> None:
    payload = json.dumps(spec).encode()
    (directory / "study.json").write_bytes(payload)
    lock = json.loads((directory / "bundle.lock.json").read_bytes())
    lock["spec_sha256"], lock["spec_size_bytes"] = hashlib.sha256(payload).hexdigest(), len(payload)
    (directory / "bundle.lock.json").write_text(json.dumps(lock))


@pytest.fixture(scope="module")
def completed(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path, dict[str, Any]]:
    root = tmp_path_factory.mktemp("cpu-study")
    bundle = root / "bundle"
    builder().build_bundle(bundle)
    output = root / "output"
    return bundle, output, run_cpu_study(bundle, output_dir=output)


@pytest.fixture
def retained(completed: tuple[Path, Path, dict[str, Any]], tmp_path: Path) -> tuple[Path, Path, dict[str, Any]]:
    bundle, output, result = completed
    return (Path(shutil.copytree(bundle, tmp_path / "bundle")),
            Path(shutil.copytree(output, tmp_path / "output")), result)


def test_real_cpu_conditions_and_causal_acceptance(completed: tuple[Path, Path, dict[str, Any]]) -> None:
    _, output, result = completed
    assert result["model_execution_performed"] is True and result["gpu_execution_deferred"] is True
    assert result["promotion_eligible"] is result["benchmark_result_claimed"] is False
    assert result["controlled_accuracy"] == {"base": 0.2, "helpful": 0.4, "harmful": 0.0, "restored": 0.2}
    assert result["reserved_usage"] == {"inference_calls": 95, "input_tokens": 200, "output_tokens": 95}
    assert result["audit_status"] == "completed"
    for role in ("practice", "confirmation", "audit"):
        report = _read_record(output / "reports" / f"{role}.json")
        expected = {"A": 0.2, "C": 0.6, "W": 0.4, "CW": 0.8, "D": 1.0 if role == "practice" else 0.8}
        assert {key: value["accuracy"] for key, value in report["conditions"].items()} == expected
    for path in (output / "claims").iterdir():
        claim = _read_record(path)
        for request in claim["requests"]:
            assert not ({"expected", "gold", "scorer", "responses"} & request.keys())
    assert _read_record(output / "selection.json")["kind"] == "fixed-before-inference"


def test_exact_resume_does_no_inference_or_materialization(
    retained: tuple[Path, Path, dict[str, Any]], monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle, output, result = retained
    before = files(output)
    monkeypatch.setattr(cpu_study, "run_cpu_batch", forbid)
    monkeypatch.setattr(TensorCandidateRegistry, "materialize", forbid)
    assert run_cpu_study(bundle, output_dir=output) == result
    assert files(output) == before


def test_fresh_cpu_runs_share_semantic_evidence(completed: tuple[Path, Path, dict[str, Any]], tmp_path: Path) -> None:
    bundle, output, result = completed
    fresh = tmp_path / "fresh"
    repeated = run_cpu_study(bundle, output_dir=fresh)
    assert repeated["semantic_digest"] == result["semantic_digest"]
    assert repeated["receipt_digests"] == result["receipt_digests"]
    assert files(output / "reports") == files(fresh / "reports")
    # Actual elapsed time and RSS are retained separately, not declared deterministic.
    assert repeated["receipt_evidence_digests"] != result["receipt_evidence_digests"]


def test_veto_withholds_audit_and_replays_without_dispatch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bundle, output = tmp_path / "veto-bundle", tmp_path / "output"
    builder().build_bundle(bundle, confirmation_outcome="veto")
    result = run_cpu_study(bundle, output_dir=output)
    assert result["confirmation"]["outcomes"] == {"C": True, "W": True, "D": False}
    assert result["audit_status"] == "withheld" and result["reserved_usage"]["inference_calls"] == 70
    assert not (output / "reports" / "audit.json").exists()
    before = files(output)
    monkeypatch.setattr(cpu_study, "run_cpu_batch", forbid)
    assert run_cpu_study(bundle, output_dir=output) == result
    assert files(output) == before


def test_validate_is_readonly_and_builder_reproducible(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    first, second = tmp_path / "first", tmp_path / "second"
    builder().build_bundle(first)
    builder().build_bundle(second)
    assert files(first) == files(second)
    before = files(first)
    monkeypatch.setattr(cpu_study, "run_cpu_batch", forbid)
    monkeypatch.setattr(TensorCandidateRegistry, "materialize", forbid)
    result = validate_cpu_study(first)
    assert result["planned_reservation"]["inference_calls"] == 95
    assert result["model_execution_performed"] is False
    assert files(first) == before
    assert not (first / "unused-validation-store").exists()
    with pytest.raises(ValueError, match="fresh directory"):
        builder().build_bundle(first)


def test_checked_in_bundle_rebuilds_exactly(tmp_path: Path) -> None:
    rebuilt = tmp_path / "bundle"
    builder().build_bundle(rebuilt)
    assert files(rebuilt) == files(EXAMPLE / "bundle")
    assert validate_cpu_study(EXAMPLE / "bundle")["planned_reservation"]["inference_calls"] == 95


@pytest.mark.parametrize("field", ["max_inference_calls", "max_input_tokens", "max_output_tokens"])
def test_underbudget_refused_before_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, field: str) -> None:
    bundle, output = tmp_path / "bundle", tmp_path / "output"
    builder().build_bundle(bundle)
    spec = json.loads((bundle / "study.json").read_bytes())
    spec["budget"][field] = 1
    repin(bundle, spec)
    monkeypatch.setattr(cpu_study, "run_cpu_batch", forbid)
    with pytest.raises(ValueError, match="reserved inference budget"):
        run_cpu_study(bundle, output_dir=output)
    assert not output.exists()


def test_interrupted_claim_is_charged_without_second_dispatch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bundle, output = tmp_path / "bundle", tmp_path / "output"
    builder().build_bundle(bundle)
    def interrupted(*args: Any, **kwargs: Any) -> Any:
        raise ValueError("worker interrupted")
    monkeypatch.setattr(cpu_study, "run_cpu_batch", interrupted)
    with pytest.raises(ValueError, match="worker interrupted"):
        run_cpu_study(bundle, output_dir=output)
    claims = list((output / "claims").iterdir())
    assert len(claims) == 1 and _read_record(claims[0])["reserved"]["inference_calls"] == 5
    monkeypatch.setattr(cpu_study, "run_cpu_batch", forbid)
    with pytest.raises(ValueError, match="remains charged"):
        run_cpu_study(bundle, output_dir=output)


@pytest.mark.parametrize("missing", ["claims/control-base.json", "results/control-base.json", "claims/practice-A.json",
                                     "selection.json", "seals/helpful.json", "confirmation.json", "reports/audit.json"])
def test_missing_evidence_refused_without_repair(
    retained: tuple[Path, Path, dict[str, Any]], monkeypatch: pytest.MonkeyPatch, missing: str,
) -> None:
    bundle, output, _ = retained
    target = output / missing
    target.unlink()
    monkeypatch.setattr(cpu_study, "run_cpu_batch", forbid)
    with pytest.raises(ValueError):
        run_cpu_study(bundle, output_dir=output)
    assert not target.exists()


def test_partial_prefix_gap_cannot_reexecute_earlier_batch(
    retained: tuple[Path, Path, dict[str, Any]], monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle, output, _ = retained
    (output / "complete.json").unlink()
    (output / "confirmation.json").unlink()
    for name in ("claims/control-base.json", "results/control-base.json"):
        (output / name).unlink()
    monkeypatch.setattr(cpu_study, "run_cpu_batch", forbid)
    with pytest.raises(ValueError, match="ordered prefix"):
        run_cpu_study(bundle, output_dir=output)


@pytest.mark.parametrize("candidate", ["helpful", "harmful"])
def test_every_used_checkpoint_is_required_on_resume(
    retained: tuple[Path, Path, dict[str, Any]], monkeypatch: pytest.MonkeyPatch, candidate: str,
) -> None:
    bundle, output, _ = retained
    spec = load_cpu_study(bundle)
    selected = next(c for c in spec.candidates if c.candidate_id == candidate)
    path = output / "materializations" / selected.digest / "model.safetensors"
    path.unlink()
    monkeypatch.setattr(cpu_study, "run_cpu_batch", forbid)
    monkeypatch.setattr(TensorCandidateRegistry, "materialize", forbid)
    with pytest.raises((ValueError, FileNotFoundError)):
        run_cpu_study(bundle, output_dir=output)
    assert not path.exists()


@pytest.mark.parametrize("mutation", ["runtime", "text", "telemetry", "integer_flag"])
def test_rehashed_receipt_tampering_rejected(
    retained: tuple[Path, Path, dict[str, Any]], monkeypatch: pytest.MonkeyPatch, mutation: str,
) -> None:
    bundle, output, _ = retained
    path = output / "results" / "control-base.json"
    payload = _read_record(path)
    if mutation == "runtime":
        payload["runtime_digest"] = "0" * 64
    elif mutation == "text":
        payload["results"][0]["text"] = "one"
    elif mutation == "telemetry":
        payload["telemetry"]["elapsed_seconds"] += 0.01
    else:
        payload["model_execution_performed"] = 1
    path.write_text(json.dumps({"payload": payload, "digest": canonical_digest(payload)}))
    monkeypatch.setattr(cpu_study, "run_cpu_batch", forbid)
    with pytest.raises(ValueError):
        run_cpu_study(bundle, output_dir=output)


def test_unknown_retained_entries_fail_before_dispatch(retained: tuple[Path, Path, dict[str, Any]],
                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    bundle, output, _ = retained
    (output / "claims" / "unknown.json").write_text("{}")
    monkeypatch.setattr(cpu_study, "run_cpu_batch", forbid)
    with pytest.raises(ValueError, match="ordered prefix"):
        run_cpu_study(bundle, output_dir=output)


@pytest.mark.parametrize("directory", ["reports", "seals"])
def test_unknown_derived_evidence_rejected_before_partial_resume(
    retained: tuple[Path, Path, dict[str, Any]], monkeypatch: pytest.MonkeyPatch, directory: str,
) -> None:
    bundle, output, _ = retained
    (output / "complete.json").unlink()
    (output / directory / "unknown.json").write_text("{}")
    monkeypatch.setattr(cpu_study, "run_cpu_batch", forbid)
    with pytest.raises(ValueError, match="unowned"):
        run_cpu_study(bundle, output_dir=output)


def test_output_symlinks_and_unowned_files_rejected(completed: tuple[Path, Path, dict[str, Any]], tmp_path: Path) -> None:
    bundle, _, _ = completed
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "link"
    link.symlink_to(target, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        run_cpu_study(bundle, output_dir=link)
    (target / "user-file").write_text("preserve")
    with pytest.raises(ValueError, match="unowned"):
        run_cpu_study(bundle, output_dir=target)
    assert (target / "user-file").read_text() == "preserve"


@pytest.mark.parametrize("name", ["study.json", "base.safetensors"])
def test_bundle_tampering_rejected(retained: tuple[Path, Path, dict[str, Any]], name: str) -> None:
    bundle, _, _ = retained
    path = bundle / name
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(ValueError, match="byte budget|byte/hash"):
        validate_cpu_study(bundle)
