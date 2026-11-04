from __future__ import annotations

import json
import os
import shutil
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

from autocontext.weight_evolution import cpu_campaign, cpu_search
from autocontext.weight_evolution.artifacts import canonical_digest
from autocontext.weight_evolution.checkpoint_candidates import TensorCandidateRegistry
from autocontext.weight_evolution.cpu_campaign import report_cpu_campaign, run_cpu_campaign, validate_cpu_campaign
from autocontext.weight_evolution.cpu_campaign_models import CpuCampaignRepeat
from autocontext.weight_evolution.cpu_campaign_tasks import build_cpu_campaign
from autocontext.weight_evolution.session import _read_record

pytestmark = pytest.mark.skipif(sys.platform not in {"linux", "darwin"}, reason="CPU campaigns require POSIX limits")


def files(root: Path) -> dict[str, bytes]:
    return {str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file()}


def forbid(*args: Any, **kwargs: Any) -> Any:
    raise AssertionError("retained campaign dispatched or wrote new work")


def build(directory: Path, *, seeds: tuple[int, ...] = (31, 47)) -> None:
    build_cpu_campaign(directory, task_seeds=seeds, cases_per_role=2,
                       repeats=(CpuCampaignRepeat(repeat_id="r0", inference_seed=406, search_order_seed=701),))


@pytest.fixture(scope="module")
def completed(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path, dict[str, Any]]:
    root = tmp_path_factory.mktemp("cpu-campaign")
    bundle, output = root / "bundle", root / "output"
    build(bundle)
    return bundle, output, run_cpu_campaign(bundle, output_dir=output)


@pytest.fixture
def retained(completed: tuple[Path, Path, dict[str, Any]], tmp_path: Path) -> tuple[Path, Path, dict[str, Any]]:
    bundle, output, report = completed
    return Path(shutil.copytree(bundle, tmp_path / "bundle")), Path(shutil.copytree(output, tmp_path / "output")), report


def rehash(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps({"payload": payload, "digest": canonical_digest(payload)}))


def test_complete_campaign_has_every_paired_policy_and_equal_search_budget(
    completed: tuple[Path, Path, dict[str, Any]],
) -> None:
    bundle, output, report = completed
    validation = validate_cpu_campaign(bundle)
    assert validation["run_count"] == 6 and validation["task_count"] == 2
    assert validation["model_execution_performed"] is False
    rows = report["rows"]
    assert len(rows) == 6 and all(row["status"] == "completed" for row in rows)
    for task_id in {row["task_id"] for row in rows}:
        task_rows = [row for row in rows if row["task_id"] == task_id]
        assert {r["policy"] for r in task_rows} == {"adaptive-v1", "fixed-order-v1", "seeded-random-v1"}
        assert len({canonical_digest(row["reserved_capacity"]) for row in task_rows}) == 1
        assert {row["inference_seed"] for row in task_rows} == {406}
        assert {row["charged_usage"]["proposals"] for row in task_rows} == {9}
        children = [_read_record(output / "searches" / row["run_id"] / "complete.json") for row in task_rows]
        assert len({canonical_digest(child["search_capacity_per_mechanism"]) for child in children}) == 1
        for child in children:
            assert all(child["restoration_verified"].values())
            for mechanism in ("C", "W", "D"):
                assert child["search_usage"][mechanism]["inference_calls"] == 10
                history = child["histories"][mechanism]
                expected = max(history, key=lambda entry: entry["correct"])["option_id"]
                assert child["selected_options"][mechanism] == expected
    assert report["gpu_execution_deferred"] is True
    assert report["promotion_eligible"] is False


def test_validate_and_report_are_read_only_even_without_write_permission(
    completed: tuple[Path, Path, dict[str, Any]], monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle, output, report = completed
    before = files(output)
    original_open = os.open

    def read_only_open(path: Any, flags: int, *args: Any, **kwargs: Any) -> int:
        assert not flags & (os.O_CREAT | os.O_RDWR | os.O_WRONLY | os.O_TRUNC | os.O_APPEND)
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", read_only_open)
    monkeypatch.setattr(cpu_campaign, "run_cpu_search", forbid)
    monkeypatch.setattr(cpu_search, "run_cpu_batch", forbid)
    monkeypatch.setattr(TensorCandidateRegistry, "materialize", forbid)
    monkeypatch.setattr(Path, "mkdir", forbid)
    assert validate_cpu_campaign(bundle)["run_count"] == 6
    assert report_cpu_campaign(bundle, output_dir=output) == report
    assert files(output) == before


def test_completed_resume_preserves_bytes_and_never_dispatches(
    retained: tuple[Path, Path, dict[str, Any]], monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle, output, report = retained
    before = files(output)
    monkeypatch.setattr(cpu_campaign, "run_cpu_search", forbid)
    monkeypatch.setattr(cpu_search, "run_cpu_batch", forbid)
    assert run_cpu_campaign(bundle, output_dir=output) == report
    assert files(output) == before


def test_missing_derived_campaign_report_can_be_regenerated_without_inference(
    retained: tuple[Path, Path, dict[str, Any]], monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle, output, report = retained
    (output / "report.json").unlink()
    monkeypatch.setattr(cpu_campaign, "run_cpu_search", forbid)
    assert report_cpu_campaign(bundle, output_dir=output) == report
    assert not (output / "report.json").exists()
    assert run_cpu_campaign(bundle, output_dir=output) == report
    assert _read_record(output / "report.json") == report


@pytest.mark.parametrize("target", ["receipt", "checkpoint", "bundle", "lock", "report", "claim", "result", "manifest"])
def test_retained_tampering_rejects_before_dispatch(
    retained: tuple[Path, Path, dict[str, Any]], monkeypatch: pytest.MonkeyPatch, target: str,
) -> None:
    bundle, output, report = retained
    run_id = report["rows"][0]["run_id"]
    child = output / "searches" / run_id
    if target == "receipt":
        path = sorted((child / "results").iterdir())[-1]
        value = _read_record(path)
        value["value"]["results"][0]["text"] = "tampered"
        rehash(path, value)
    elif target == "checkpoint":
        path = next((child / "materializations").glob("*/model.safetensors"))
        path.write_bytes(path.read_bytes()[:-1] + b"x")
    elif target == "bundle":
        (output / "bundles" / run_id / "study.json").write_text("{}")
    elif target == "lock":
        (child / ".cpu-search.lock").unlink()
    elif target == "report":
        path = output / "report.json"
        value = _read_record(path)
        value["rows"] = value["rows"][:-1]
        rehash(path, value)
    elif target == "claim":
        path = output / "claims" / "00000000.json"
        value = _read_record(path)
        value["run"]["reserved_capacity"]["proposals"] -= 1
        rehash(path, value)
    elif target == "result":
        path = output / "results" / "00000000.json"
        value = _read_record(path)
        value["row"]["confirmation_scores"]["C"] = 99.0
        rehash(path, value)
    else:
        path = output / "manifest.json"
        value = _read_record(path)
        value["runtime_digest"] = "0" * 64
        rehash(path, value)
    monkeypatch.setattr(cpu_campaign, "run_cpu_search", forbid)
    with pytest.raises(ValueError):
        run_cpu_campaign(bundle, output_dir=output)


def test_missing_middle_result_and_unclaimed_children_reject(
    retained: tuple[Path, Path, dict[str, Any]], monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle, output, _ = retained
    monkeypatch.setattr(cpu_campaign, "run_cpu_search", forbid)
    (output / "results" / "00000000.json").unlink()
    with pytest.raises(ValueError, match="skipped"):
        run_cpu_campaign(bundle, output_dir=output)


def test_unclaimed_child_rejects(retained: tuple[Path, Path, dict[str, Any]]) -> None:
    bundle, output, _ = retained
    (output / "searches" / "unclaimed").mkdir()
    with pytest.raises(ValueError, match="unclaimed"):
        report_cpu_campaign(bundle, output_dir=output)


def test_inventory_caps_traversal_before_hashing_any_unbounded_tree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for index in range(1100):
        (tmp_path / f"entry-{index:04}").touch()
    original = os.scandir
    visited = 0

    @contextmanager
    def bounded_scandir(path: Any) -> Iterator[Iterator[Any]]:
        def counted(entries: Any) -> Iterator[Any]:
            nonlocal visited
            for entry in entries:
                visited += 1
                yield entry

        with original(path) as entries:
            yield counted(entries)

    monkeypatch.setattr(os, "scandir", bounded_scandir)
    monkeypatch.setattr(cpu_campaign, "file_identity", forbid)
    with pytest.raises(ValueError, match="file bound"):
        cpu_campaign._inventory(tmp_path)
    assert visited == 1025


@pytest.mark.parametrize("corrupt", ["result", "checkpoint", "completed-marker"])
def test_pending_child_validates_earlier_evidence_before_later_dispatch(
    retained: tuple[Path, Path, dict[str, Any]], monkeypatch: pytest.MonkeyPatch, corrupt: str,
) -> None:
    bundle, output, report = retained
    run_id = report["rows"][0]["run_id"]
    child = output / "searches" / run_id
    (output / "report.json").unlink()
    for folder in ("claims", "results"):
        for path in (output / folder).iterdir():
            if folder == "results" or path.name != "00000000.json":
                path.unlink()
    for folder in ("searches", "bundles"):
        for path in (output / folder).iterdir():
            if path.name != run_id:
                shutil.rmtree(path)
    if corrupt == "completed-marker":
        # The child was complete, but parent publication was interrupted. A
        # missing last child result is corruption, not an ordinary failed run.
        sorted((child / "results").iterdir())[-1].unlink()
    else:
        # Five completed materializations, followed by an uncertain baseline probe.
        for folder in ("claims", "results"):
            for path in (child / folder).iterdir():
                if int(path.stem) > (5 if folder == "claims" else 4):
                    path.unlink()
        for name in ("complete.json", "confirmation.json", "selection.json"):
            (child / name).unlink()
        for path in (child / "reports").iterdir():
            path.unlink()
    if corrupt == "result":
        path = child / "results" / "00000000.json"
        raw = _read_record(path)
        raw["value"]["changed_entries"] += 1
        rehash(path, raw)
    elif corrupt == "checkpoint":
        path = next((child / "materializations").glob("*/model.safetensors"))
        path.write_bytes(path.read_bytes()[:-1] + b"x")
    monkeypatch.setattr(cpu_search, "run_cpu_batch", forbid)
    with pytest.raises(ValueError):
        run_cpu_campaign(bundle, output_dir=output)
    assert not (output / "results" / "00000000.json").exists()
    assert len(list((output / "claims").iterdir())) == 1


def test_task_and_source_pins_reject(retained: tuple[Path, Path, dict[str, Any]], monkeypatch: pytest.MonkeyPatch) -> None:
    bundle, _, _ = retained
    monkeypatch.setattr(cpu_campaign, "campaign_implementation_digest", lambda: "0" * 64)
    with pytest.raises(ValueError, match="implementation source"):
        validate_cpu_campaign(bundle)


def test_symlink_output_rejects(completed: tuple[Path, Path, dict[str, Any]], tmp_path: Path) -> None:
    bundle, output, _ = completed
    link = tmp_path / "alias"
    link.symlink_to(output, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        report_cpu_campaign(bundle, output_dir=link)


@pytest.mark.parametrize("uncertain", [False, True])
def test_failures_remain_reserved_and_other_runs_complete_without_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, uncertain: bool,
) -> None:
    bundle, output = tmp_path / "bundle", tmp_path / "output"
    build(bundle)
    original = cpu_search.run_cpu_batch
    calls = 0

    def fail_once(*args: Any, **kwargs: Any) -> Any:
        nonlocal calls
        calls += 1
        if calls == 1:
            if uncertain:
                raise KeyboardInterrupt("unknown worker outcome")
            raise ValueError("known worker failure")
        return original(*args, **kwargs)

    monkeypatch.setattr(cpu_search, "run_cpu_batch", fail_once)
    if uncertain:
        with pytest.raises(KeyboardInterrupt):
            run_cpu_campaign(bundle, output_dir=output)
        partial = report_cpu_campaign(bundle, output_dir=output)
        assert [row["status"] for row in partial["rows"]] == ["pending", *(["not-started"] * 5)]
    report = run_cpu_campaign(bundle, output_dir=output)
    assert [row["status"] for row in report["rows"]] == ["failed", *(["completed"] * 5)]
    failed = report["rows"][0]
    assert failed["charged_usage"] is None and failed["confirmation_scores"] is None
    assert failed["reserved_capacity"]["inference_calls"] > 0
    child = output / "searches" / failed["run_id"]
    claimed = [_read_record(path) for path in sorted((child / "claims").iterdir())]
    assert sum(claim["kind"] == "inference" for claim in claimed) == 1
    before = files(output)
    monkeypatch.setattr(cpu_search, "run_cpu_batch", forbid)
    assert run_cpu_campaign(bundle, output_dir=output) == report
    assert report_cpu_campaign(bundle, output_dir=output) == report
    assert files(output) == before


def test_completed_child_survives_interrupted_parent_publication(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bundle, output = tmp_path / "bundle", tmp_path / "output"
    build(bundle)
    original_publish = cpu_campaign._immutable_json

    def interrupt(path: Path, payload: dict[str, Any]) -> None:
        if path == output / "results" / "00000000.json":
            raise KeyboardInterrupt("child complete, parent unpublished")
        original_publish(path, payload)

    monkeypatch.setattr(cpu_campaign, "_immutable_json", interrupt)
    with pytest.raises(KeyboardInterrupt):
        run_cpu_campaign(bundle, output_dir=output)
    first = next((output / "searches").iterdir())
    before = files(first)
    monkeypatch.setattr(cpu_campaign, "_immutable_json", original_publish)
    original_run = cpu_search.run_cpu_batch

    def protect_completed(*args: Any, **kwargs: Any) -> Any:
        assert first not in kwargs["checkpoint_path"].parents
        # Baseline requests use the corresponding derived bundle.
        assert first.name not in kwargs["checkpoint_path"].parts
        return original_run(*args, **kwargs)

    monkeypatch.setattr(cpu_search, "run_cpu_batch", protect_completed)
    report = run_cpu_campaign(bundle, output_dir=output)
    assert all(row["status"] == "completed" for row in report["rows"])
    assert files(first) == before


def test_fresh_campaigns_share_semantic_report(
    completed: tuple[Path, Path, dict[str, Any]], tmp_path: Path,
) -> None:
    bundle, _, report = completed
    fresh = run_cpu_campaign(bundle, output_dir=tmp_path / "fresh")
    assert fresh["semantic_digest"] == report["semantic_digest"]
