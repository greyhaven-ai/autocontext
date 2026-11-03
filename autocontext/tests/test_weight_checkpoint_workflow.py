from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import socket
import struct
import subprocess
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from autocontext.weight_evolution.adaptive_provider import OfflineFeedbackProvider
from autocontext.weight_evolution.artifacts import TensorFile
from autocontext.weight_evolution.checkpoint_candidates import TensorCandidateRegistry, TensorSessionAdapter
from autocontext.weight_evolution.checkpoint_workflow import (
    load_checkpoint_workflow,
    run_checkpoint_workflow,
    validate_checkpoint_workflow,
)

EXAMPLE = Path(__file__).resolve().parents[2] / "examples" / "weight_evolution" / "qwen_checkpoints"


def builder() -> ModuleType:
    spec = importlib.util.spec_from_file_location("checkpoint_example_builder", EXAMPLE / "build_fixture.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run(output: Path, fixture: Path = EXAMPLE / "fixtures") -> dict[str, Any]:
    return run_checkpoint_workflow(fixture, output_dir=output)


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


def test_actual_tensor_edits_survive_selection_restoration_and_native_confirmation(tmp_path: Path) -> None:
    output = tmp_path / "run"
    result = run(output)
    workflow, _, _, _ = load_checkpoint_workflow(EXAMPLE / "fixtures")
    target = next(candidate for candidate in workflow.candidates if candidate.candidate_id == "tiny-selected")
    base = TensorFile(EXAMPLE / "fixtures" / "base.safetensors")
    edited = TensorFile(output / "materializations" / target.digest / "model.safetensors")
    assert edited.read("layer.0.weight") == bytes.fromhex("4040004040408040")
    assert edited.read("layer.1.weight") == struct.pack("<ff", 1.0, 2.0)
    assert edited.read("embedding.weight") == base.read("embedding.weight")
    assert edited.read("norm.weight") == base.read("norm.weight")
    assert result["tensor_edit_performed"] is True
    assert result["model_execution_performed"] is result["promotion_eligible"] is result["benchmark_result_claimed"] is False
    assert result["audit_status"] == "completed_fixture_only"
    assert all(row["status"] == "confirmed" for row in result["confirmation_outcomes"])
    for mechanism, calls in (("C", 9), ("W", 12), ("D", 12)):
        native = record(output / f"native-{mechanism}.json")
        completed = record(output / "search" / mechanism / "complete.json")
        assert native["representation"] == "verified_safetensors_fixture"
        assert native["source"]["declared_search_calls"] == completed["reserved_usage"]["operations"] == calls
        assert completed["selected_round"] == 2
        assert native["tensor_edit_performed"] is (mechanism == "W")
        assert native["checkpoint"]["changed_entries"] == (2 if mechanism == "W" else 0)
    weight = record(output / "native-W.json")
    complete = record(output / "search" / "W" / "complete.json")
    assert complete["restoration_performed"] is True
    assert weight["candidate_digest"] != weight["selected_candidate_digest"]
    assert weight["checkpoint"]["reference"] == target.reference.to_dict()
    receipts = [json.loads(path.read_bytes()) for path in (output / "materializations").glob("*/receipt.json")]
    assert len(receipts) == 3 and all(receipt["changed_entries"] == 2 for receipt in receipts)


def test_resume_verifies_real_bytes_without_any_edit_or_provider_dispatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "run"
    first = run(output)
    before = files(output)

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("completed workflow dispatched work")

    monkeypatch.setattr(TensorSessionAdapter, "execute", forbidden)
    monkeypatch.setattr(TensorCandidateRegistry, "materialize", forbidden)
    monkeypatch.setattr(OfflineFeedbackProvider, "complete", forbidden)
    assert run(output) == first
    assert files(output) == before


@pytest.mark.parametrize("candidate_id", ["tiny-weak", "tiny-selected", "tiny-regressed"])
@pytest.mark.parametrize("mutation", ["delete", "corrupt"])
def test_missing_or_changed_any_used_checkpoint_blocks_resume_without_recreation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, candidate_id: str, mutation: str,
) -> None:
    output = tmp_path / "run"
    run(output)
    workflow, _, _, _ = load_checkpoint_workflow(EXAMPLE / "fixtures")
    candidate = next(item for item in workflow.candidates if item.candidate_id == candidate_id)
    path = output / "materializations" / candidate.digest / "model.safetensors"
    if mutation == "delete":
        path.unlink()
    else:
        path.write_bytes(path.read_bytes() + b"bad")

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("missing evidence was rematerialized")

    monkeypatch.setattr(TensorCandidateRegistry, "materialize", forbidden)
    with pytest.raises((ValueError, FileNotFoundError)):
        run(output)
    assert not path.exists() if mutation == "delete" else path.read_bytes().endswith(b"bad")


@pytest.mark.parametrize("filename", ["native-C.json", "native-W.json", "native-D.json", "selection.json"])
def test_completed_confirmation_does_not_repair_missing_native_evidence(tmp_path: Path, filename: str) -> None:
    output = tmp_path / "run"
    run(output)
    (output / filename).unlink()
    with pytest.raises(ValueError, match="missing its native selection evidence"):
        run(output)
    assert not (output / filename).exists()


def test_separate_confirmation_veto_prevents_audit(tmp_path: Path) -> None:
    fixture = tmp_path / "veto-fixture"
    builder().build_fixture(fixture, confirmation_outcome="veto")
    output = tmp_path / "run"
    result = run(output, fixture)
    assert [row["status"] for row in result["confirmation_outcomes"]] == ["confirmed", "confirmed", "vetoed"]
    assert result["audit_status"] == "withheld_after_confirmation_veto"
    assert not (output / "audit").exists()
    with pytest.raises(ValueError, match="immutable workflow artifact changed"):
        run(output)


def test_same_veto_fixture_resumes_without_dispatch_or_audit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixture = tmp_path / "veto-fixture"
    builder().build_fixture(fixture, confirmation_outcome="veto")
    output = tmp_path / "run"
    first = run(output, fixture)
    before = files(output)

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("completed veto workflow dispatched work")

    monkeypatch.setattr(TensorSessionAdapter, "execute", forbidden)
    monkeypatch.setattr(TensorCandidateRegistry, "materialize", forbidden)
    monkeypatch.setattr(OfflineFeedbackProvider, "complete", forbidden)
    assert run(output, fixture) == first
    assert files(output) == before
    assert first["audit_status"] == "withheld_after_confirmation_veto"
    assert not (output / "audit").exists()


def test_readonly_validate_and_deterministic_builder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    before = files(EXAMPLE / "fixtures")

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("validate attempted tensor edit or adapter execution")

    monkeypatch.setattr(TensorCandidateRegistry, "materialize", forbidden)
    monkeypatch.setattr(TensorSessionAdapter, "execute", forbidden)
    result = validate_checkpoint_workflow(EXAMPLE / "fixtures")
    assert result["candidate_count"] == 4 and result["model_execution_performed"] is False
    assert files(EXAMPLE / "fixtures") == before
    target = tmp_path / "rebuilt"
    builder().build_fixture(target)
    assert files(target) == before
    with pytest.raises(ValueError, match="new directory"):
        builder().build_fixture(target)


def test_no_network_process_or_model_backend(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("tiny CPU fixture attempted external execution")

    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    assert run(tmp_path / "run")["model_execution_performed"] is False


@pytest.mark.parametrize("name", ["data", "workflow", "confirmation", "audit"])
def test_bundle_byte_tampering_fails(bundle: Path, name: str) -> None:
    path = bundle / f"{name}.json"
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(ValueError, match="size/hash"):
        load_checkpoint_workflow(bundle)


@pytest.mark.parametrize("name", ["workflow", "confirmation", "audit"])
def test_duplicate_keys_rejected_after_repin(bundle: Path, name: str) -> None:
    raw = (bundle / f"{name}.json").read_bytes()
    repin(bundle, name, b'{"schema_version":1,' + raw[1:])
    with pytest.raises(ValueError, match="duplicate JSON key"):
        load_checkpoint_workflow(bundle)


def test_base_tampering_rejected_before_output(bundle: Path, tmp_path: Path) -> None:
    path = bundle / "base.safetensors"
    path.write_bytes(path.read_bytes() + b"changed")
    with pytest.raises(ValueError, match="base checkpoint"):
        run(tmp_path / "run", bundle)
    assert not (tmp_path / "run").exists()


@pytest.mark.parametrize("filename", ["workflow.json", "native-W.json", "complete.json"])
def test_retained_evidence_tampering_rejected(tmp_path: Path, filename: str) -> None:
    output = tmp_path / "run"
    run(output)
    (output / filename).write_text("{}")
    with pytest.raises(ValueError, match="changed|record|differs"):
        run(output)


def test_symlink_and_unowned_output_rejected(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "link"
    link.symlink_to(target, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        run(link)
    (target / "user-file").write_text("preserve")
    with pytest.raises(ValueError, match="unowned"):
        run(target)
    assert (target / "user-file").read_text() == "preserve"
