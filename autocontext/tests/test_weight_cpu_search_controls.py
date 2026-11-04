"""Real CPU control policies and verification without writable evidence access."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any

import pytest

from autocontext.weight_evolution import checkpoint_candidates, cpu_search
from autocontext.weight_evolution.checkpoint_candidates import TensorCandidateRegistry
from autocontext.weight_evolution.cpu_search_models import CpuSearchPolicy, CpuSearchSpec
from autocontext.weight_evolution.session import _read_record

pytestmark = pytest.mark.skipif(sys.platform not in {"linux", "darwin"}, reason="CPU workers require supported POSIX limits")
POLICIES = (CpuSearchPolicy(), CpuSearchPolicy(name="fixed-order-v1"),
            CpuSearchPolicy(name="seeded-random-v1", order_seed=406))


def _bundle(path: Path, policy: CpuSearchPolicy) -> None:
    source = Path(__file__).resolve().parents[2] / "examples/weight_evolution/cpu_adaptive/build_bundle.py"
    spec = importlib.util.spec_from_file_location("cpu_control_test_builder", source)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.build_bundle(path)
    value = json.loads((path / "study.json").read_bytes())
    value["policy"] = policy.to_dict()
    value["study_id"] = f"control-{policy.name}"
    study = CpuSearchSpec.model_validate_json(json.dumps(value))
    raw = (json.dumps(study.to_dict(), sort_keys=True, indent=2) + "\n").encode()
    (path / "study.json").write_bytes(raw)
    lock = json.loads((path / "bundle.lock.json").read_bytes())
    lock["spec_sha256"], lock["spec_size_bytes"] = hashlib.sha256(raw).hexdigest(), len(raw)
    (path / "bundle.lock.json").write_text(json.dumps(lock, sort_keys=True, indent=2) + "\n")


def _files(root: Path) -> dict[str, bytes]:
    return {str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file()}


def _forbidden(*args: Any, **kwargs: Any) -> Any:
    raise AssertionError("completed-run verification attempted a write or dispatch")


@pytest.fixture(scope="module", params=POLICIES, ids=lambda policy: policy.name)
def completed(request: pytest.FixtureRequest, tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path, dict[str, Any]]:
    root = tmp_path_factory.mktemp(f"cpu-control-{request.param.name}")
    bundle, output = root / "bundle", root / "output"
    _bundle(bundle, request.param)
    return bundle, output, cpu_search.run_cpu_search(bundle, output_dir=output)


def test_all_policies_bind_actual_decisions_and_keep_search_work_equal(
    completed: tuple[Path, Path, dict[str, Any]],
) -> None:
    bundle, output, summary = completed
    spec = cpu_search.load_cpu_search(bundle)
    expected_kind = "adaptive_cpu_inference" if spec.policy.name == "adaptive-v1" else "cpu_search_inference"
    assert summary["execution_kind"] == expected_kind
    assert summary["policy"] == spec.policy.to_dict() and summary["policy_digest"] == spec.policy.digest
    assert summary["gpu_execution_deferred"] is True
    for mechanism, usage in summary["search_usage"].items():
        assert usage["proposals"] == 3 and usage["inference_calls"] == usage["output_tokens"] == 25
        history = summary["histories"][mechanism]
        assert len(history) == 4 and all(row["total"] == 5 for row in history)
        assert summary["restoration_verified"][mechanism] is True
        for decision in summary["proposal_history"][mechanism]:
            assert decision["policy"] == spec.policy.to_dict() and decision["policy_digest"] == spec.policy.digest
    for path in (output / "claims").iterdir():
        claim = _read_record(path)
        if claim["kind"] == "proposal":
            assert claim["input"]["policy"] == spec.policy.to_dict()
            assert claim["input"]["policy_digest"] == spec.policy.digest
    selection = _read_record(output / "selection.json")
    assert selection["policy"] == spec.policy.to_dict() and selection["policy_digest"] == spec.policy.digest
    for path in (output / "reports").iterdir():
        report = _read_record(path)
        assert report["execution_kind"] == expected_kind
        assert report["policy"] == spec.policy.to_dict() and report["policy_digest"] == spec.policy.digest
    assert (output / "reports/audit.json").exists() == (summary["confirmation"]["status"] == "confirmed")
    if spec.policy.name == "fixed-order-v1":
        for mechanism, history in summary["histories"].items():
            assert [row["option_id"] for row in history[1:]] == [option.option_id for option in spec.options_for(mechanism)[:3]]


def test_completed_verifier_uses_only_read_descriptors_and_preserves_every_byte(
    completed: tuple[Path, Path, dict[str, Any]], monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle, output, expected = completed
    before = _files(output)
    real_open = os.open
    opened = []

    def readonly_open(path: str | bytes | os.PathLike[str] | os.PathLike[bytes], flags: int,
                      *args: Any, **kwargs: Any) -> int:
        assert flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND) == 0
        opened.append(str(path))
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(cpu_search, "run_cpu_batch", _forbidden)
    monkeypatch.setattr(cpu_search, "_immutable_json", _forbidden)
    monkeypatch.setattr(cpu_search, "_clear_staging", _forbidden)
    monkeypatch.setattr(cpu_search, "advisory_path_lock", _forbidden)
    monkeypatch.setattr(checkpoint_candidates, "advisory_path_lock", _forbidden)
    monkeypatch.setattr(TensorCandidateRegistry, "materialize", _forbidden)
    monkeypatch.setattr(Path, "mkdir", _forbidden)
    monkeypatch.setattr(Path, "unlink", _forbidden)
    monkeypatch.setattr(os, "open", readonly_open)
    assert cpu_search.verify_cpu_search(bundle, output_dir=output) == expected
    assert _files(output) == before
    assert str(output / ".cpu-search.lock") in opened
    assert str(output / "materializations/.tensor-candidates.lock") in opened


@pytest.mark.parametrize("missing", ["complete.json", "manifest.json", ".cpu-search.lock",
                                    "materializations/.tensor-candidates.lock", "selection.json",
                                    "reports/confirmation.json", "results/00000000.json"])
def test_verifier_rejects_missing_evidence_without_repair(
    completed: tuple[Path, Path, dict[str, Any]], tmp_path: Path, monkeypatch: pytest.MonkeyPatch, missing: str,
) -> None:
    bundle, original, _ = completed
    output = Path(shutil.copytree(original, tmp_path / "output"))
    (output / missing).unlink()
    before = _files(output)
    monkeypatch.setattr(cpu_search, "run_cpu_batch", _forbidden)
    monkeypatch.setattr(cpu_search, "_immutable_json", _forbidden)
    monkeypatch.setattr(TensorCandidateRegistry, "materialize", _forbidden)
    with pytest.raises((ValueError, OSError)):
        cpu_search.verify_cpu_search(bundle, output_dir=output)
    assert _files(output) == before


def test_verifier_does_not_create_a_missing_output(
    completed: tuple[Path, Path, dict[str, Any]], tmp_path: Path,
) -> None:
    bundle, _, _ = completed
    output = tmp_path / "absent"
    with pytest.raises(cpu_search.CpuSearchError, match="completed run"):
        cpu_search.verify_cpu_search(bundle, output_dir=output)
    assert not output.exists()
