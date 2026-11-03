"""Regression coverage for independently reviewed CPU search provenance gaps."""

from __future__ import annotations

import importlib.util
import shutil
import sys
from pathlib import Path
from typing import Any

import pytest

from autocontext.weight_evolution import cpu_search
from autocontext.weight_evolution.checkpoint_candidates import TensorCandidateRegistry
from autocontext.weight_evolution.cpu_models import CpuInferenceRequest
from autocontext.weight_evolution.cpu_runner import CpuBatchReceipt
from autocontext.weight_evolution.session import _read_record

pytestmark = pytest.mark.skipif(sys.platform not in {"linux", "darwin"}, reason="CPU search requires supported POSIX limits")


def build_bundle(path: Path) -> None:
    example = Path(__file__).resolve().parents[2] / "examples/weight_evolution/cpu_adaptive/build_bundle.py"
    module_spec = importlib.util.spec_from_file_location("cpu_adaptive_review_builder", example)
    assert module_spec is not None and module_spec.loader is not None
    builder = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(builder)
    builder.build_bundle(path)


def snapshot(root: Path) -> dict[str, bytes]:
    return {str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file()}


def forbidden(*args: Any, **kwargs: Any) -> Any:
    raise AssertionError("retained work was repeated")


@pytest.mark.parametrize("retained_materializations", [0, 1])
def test_valid_but_unclaimed_materializations_rejected_before_new_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, retained_materializations: int,
) -> None:
    bundle, output = tmp_path / "bundle", tmp_path / "output"
    build_bundle(bundle)

    def stop_before_first_probe(*args: Any, **kwargs: Any) -> Any:
        raise KeyboardInterrupt("stop after materializing the bounded checkpoint set")

    monkeypatch.setattr(cpu_search, "run_cpu_batch", stop_before_first_probe)
    with pytest.raises(KeyboardInterrupt):
        cpu_search.run_cpu_search(bundle, output_dir=output)
    assert len(list((output / "materializations").glob("*/model.safetensors"))) == 4
    # Leave a syntactically valid journal prefix while retaining outputs of
    # later materializations. Those bytes cannot belong to unclaimed work.
    for directory in ("claims", "results"):
        for path in (output / directory).iterdir():
            if int(path.stem) >= retained_materializations:
                path.unlink()
    for path in (output / "seals").iterdir():
        if retained_materializations == 0 or path.name != "base.json":
            path.unlink()
    before = snapshot(output)
    monkeypatch.setattr(cpu_search, "run_cpu_batch", forbidden)
    monkeypatch.setattr(TensorCandidateRegistry, "materialize", forbidden)
    with pytest.raises(cpu_search.CpuSearchError, match="materialization"):
        cpu_search.run_cpu_search(bundle, output_dir=output)
    assert snapshot(output) == before


@pytest.fixture(scope="module")
def complete_search(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path, dict[str, Any]]:
    root = tmp_path_factory.mktemp("cpu-search-review-complete")
    bundle, output = root / "bundle", root / "output"
    build_bundle(bundle)
    return bundle, output, cpu_search.run_cpu_search(bundle, output_dir=output)


def test_valid_prefix_resume_keeps_completed_arm_and_materializations(
    complete_search: tuple[Path, Path, dict[str, Any]], tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle, original, expected = complete_search
    output = Path(shutil.copytree(original, tmp_path / "prefix"))
    restored_claim = next(path for path in (output / "claims").iterdir()
                          if _read_record(path)["label"] == "C-restored")
    last_retained = int(restored_claim.stem)
    for directory in ("claims", "results"):
        for path in (output / directory).iterdir():
            if int(path.stem) > last_retained:
                path.unlink()
    for name in ("selection.json", "confirmation.json", "complete.json"):
        (output / name).unlink()
    for path in (output / "reports").iterdir():
        path.unlink()
    retained = snapshot(output)
    runner = cpu_search.run_cpu_batch
    new_labels: list[str] = []

    def only_unfinished(requests: tuple[CpuInferenceRequest, ...], *, checkpoint_path: Path) -> CpuBatchReceipt:
        label = requests[0].request_id.rsplit(".", 1)[0]
        assert not label.startswith("C-"), "completed C arm was dispatched twice"
        new_labels.append(label)
        return runner(requests, checkpoint_path=checkpoint_path)

    monkeypatch.setattr(TensorCandidateRegistry, "materialize", forbidden)
    monkeypatch.setattr(cpu_search, "run_cpu_batch", only_unfinished)
    actual = cpu_search.run_cpu_search(bundle, output_dir=output)
    assert actual["semantic_digest"] == expected["semantic_digest"]
    assert actual["selected_options"] == expected["selected_options"]
    assert actual["reserved_usage"] == expected["reserved_usage"]
    assert actual["audit_status"] == "completed"
    assert len(new_labels) == len(set(new_labels)) == 21
    assert new_labels[0] == "W-baseline" and new_labels[-1] == "audit-D"
    resumed = snapshot(output)
    assert {name: resumed[name] for name in retained} == retained


def test_completed_materialization_can_publish_missing_tail_seal_without_rematerializing(
    complete_search: tuple[Path, Path, dict[str, Any]], tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle, _, expected = complete_search
    spec = cpu_search.load_cpu_search(bundle)
    output = tmp_path / "seal-interruption"
    last_seal = f"seals/{spec.candidates[-1].candidate_id}.json"
    publish = cpu_search._Journal.publish

    def interrupted(journal: cpu_search._Journal, name: str, value: dict[str, Any]) -> None:
        if name == last_seal:
            raise KeyboardInterrupt("materialization result committed before its seal")
        publish(journal, name, value)

    monkeypatch.setattr(cpu_search._Journal, "publish", interrupted)
    with pytest.raises(KeyboardInterrupt, match="before its seal"):
        cpu_search.run_cpu_search(bundle, output_dir=output)
    assert not (output / last_seal).exists()
    assert len(list((output / "claims").iterdir())) == len(list((output / "results").iterdir())) == len(spec.candidates)
    retained = snapshot(output)
    monkeypatch.setattr(cpu_search._Journal, "publish", publish)
    monkeypatch.setattr(TensorCandidateRegistry, "materialize", forbidden)
    actual = cpu_search.run_cpu_search(bundle, output_dir=output)
    assert (output / last_seal).is_file()
    assert actual["semantic_digest"] == expected["semantic_digest"]
    assert actual["audit_status"] == "completed"
    resumed = snapshot(output)
    assert {name: resumed[name] for name in retained} == retained
