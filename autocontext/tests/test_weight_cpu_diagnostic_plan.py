from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from autocontext.weight_evolution import cpu_diagnostic_plan, cpu_search
from autocontext.weight_evolution.artifacts import canonical_digest
from autocontext.weight_evolution.checkpoint_candidates import TensorCandidateRegistry
from autocontext.weight_evolution.cpu_campaign import run_cpu_campaign
from autocontext.weight_evolution.cpu_campaign_models import CpuCampaignRepeat
from autocontext.weight_evolution.cpu_campaign_tasks import build_cpu_campaign
from autocontext.weight_evolution.cpu_diagnostic_plan import (
    build_cpu_diagnostic_plan,
    load_cpu_diagnostic_context,
    load_cpu_diagnostic_plan,
    validate_cpu_diagnostic_plan,
)
from autocontext.weight_evolution.session import _read_record

pytestmark = pytest.mark.skipif(sys.platform not in {"linux", "darwin"}, reason="CPU diagnostics require POSIX limits")


def files(root: Path) -> dict[str, bytes]:
    return {str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file()}


def forbid(*args: Any, **kwargs: Any) -> Any:
    raise AssertionError("plan verification must not execute models or write evidence")


@pytest.fixture(autouse=True)
def diagnostic_source_pin(monkeypatch: pytest.MonkeyPatch) -> None:
    # Plan tests can run while the separate diagnostic execution/report modules
    # are being developed. Integration tests use their actual source closure.
    monkeypatch.setattr(cpu_diagnostic_plan, "_implementation_digest", lambda: "d" * 64)


@pytest.fixture(scope="module")
def completed(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path, dict[str, Any]]:
    root = tmp_path_factory.mktemp("diagnostic-plan-source")
    bundle, output = root / "campaign", root / "source-output"
    build_cpu_campaign(bundle, task_seeds=(31, 47), cases_per_role=2,
                       repeats=(CpuCampaignRepeat(repeat_id="r0", inference_seed=406, search_order_seed=701),))
    return bundle, output, run_cpu_campaign(bundle, output_dir=output)


def test_plan_pins_full_source_and_shared_label_free_schedule(
    completed: tuple[Path, Path, dict[str, Any]], tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle, source, report = completed
    before = files(source)
    monkeypatch.setattr(cpu_search, "run_cpu_batch", forbid)
    monkeypatch.setattr(TensorCandidateRegistry, "materialize", forbid)
    plan = tmp_path / "plan"
    spec = build_cpu_diagnostic_plan(bundle, source, output=plan)
    assert len(spec.references) == 2 and spec.required_usage["inference_calls"] == 52
    assert spec.required_usage["references"] == 2 and spec.required_usage["materializations"] == 10
    assert spec.campaign_report_digest == canonical_digest(report) != report["semantic_digest"]
    assert spec.campaign_ledger_digest == report["ledger_digest"]
    assert spec.runtime_digest == report["runtime_digest"]
    for reference in spec.references:
        assert reference.inference_seed == 406
        assert len(reference.source_runs) == 3
        assert len(reference.practice) == 2
        assert all(option.decoding is None or option.decoding.seed == 406 for option in reference.options)
        for run in reference.source_runs:
            selection = _read_record(source / "searches" / run.run_id / "selection.json")
            assert run.selection_digest == canonical_digest(selection)
    assert '"expected"' not in (plan / "diagnostic.json").read_text()
    assert '"role"' not in (plan / "diagnostic.json").read_text()
    assert validate_cpu_diagnostic_plan(plan, campaign_bundle=bundle, campaign_output=source) == spec
    assert files(source) == before
    build_cpu_diagnostic_plan(bundle, source, output=tmp_path / "rebuilt")
    assert files(tmp_path / "rebuilt") == files(plan)


def test_plan_validation_is_read_only(
    completed: tuple[Path, Path, dict[str, Any]], tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle, source, _ = completed
    plan = tmp_path / "plan"
    expected = build_cpu_diagnostic_plan(bundle, source, output=plan)
    before = files(plan), files(source)
    original_open = os.open

    def readonly(path: Any, flags: int, *args: Any, **kwargs: Any) -> int:
        assert not flags & (os.O_CREAT | os.O_RDWR | os.O_WRONLY | os.O_TRUNC | os.O_APPEND)
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", readonly)
    monkeypatch.setattr(Path, "mkdir", forbid)
    monkeypatch.setattr(Path, "write_bytes", forbid)
    monkeypatch.setattr(Path, "write_text", forbid)
    monkeypatch.setattr(cpu_search, "run_cpu_batch", forbid)
    monkeypatch.setattr(TensorCandidateRegistry, "materialize", forbid)
    assert validate_cpu_diagnostic_plan(plan, campaign_bundle=bundle, campaign_output=source) == expected
    assert (files(plan), files(source)) == before


def test_loaded_context_verifies_source_once_and_returns_original_scorer_cases(
    completed: tuple[Path, Path, dict[str, Any]], tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle, source, report = completed
    plan = tmp_path / "plan"
    expected = build_cpu_diagnostic_plan(bundle, source, output=plan)
    original = cpu_diagnostic_plan.verify_cpu_diagnostic_source
    calls = 0

    def tracked(*args: Any, **kwargs: Any) -> Any:
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(cpu_diagnostic_plan, "verify_cpu_diagnostic_source", tracked)
    spec, templates, verified_report = load_cpu_diagnostic_context(plan, campaign_bundle=bundle, campaign_output=source)
    assert calls == 1 and spec == expected and verified_report == report
    for reference in spec.references:
        cases = [case.to_dict() for case in templates[reference.task_id].cases if case.role == "practice"]
        assert canonical_digest(cases) == reference.practice_cases_digest


@pytest.mark.parametrize("target", ["selection", "checkpoint", "receipt", "missing-selection"])
def test_changed_source_rejects_before_plan_publication(
    completed: tuple[Path, Path, dict[str, Any]], tmp_path: Path, monkeypatch: pytest.MonkeyPatch, target: str,
) -> None:
    bundle, original, report = completed
    source = Path(shutil.copytree(original, tmp_path / "source"))
    child = source / "searches" / report["rows"][0]["run_id"]
    if target == "checkpoint":
        checkpoint = next((child / "materializations").glob("*/model.safetensors"))
        checkpoint.write_bytes(checkpoint.read_bytes()[:-1] + b"x")
    elif target == "missing-selection":
        (child / "selection.json").unlink()
    else:
        path = child / "selection.json" if target == "selection" else sorted((child / "results").iterdir())[-1]
        value = _read_record(path)
        value["unexpected"] = True
        path.write_text(json.dumps({"payload": value, "digest": canonical_digest(value)}))
    before = files(source)
    monkeypatch.setattr(cpu_search, "run_cpu_batch", forbid)
    monkeypatch.setattr(TensorCandidateRegistry, "materialize", forbid)
    with pytest.raises(ValueError):
        build_cpu_diagnostic_plan(bundle, source, output=tmp_path / "plan")
    assert not (tmp_path / "plan").exists()
    assert files(source) == before


@pytest.mark.parametrize("target", ["bytes", "repinned-source", "selection-pin", "implementation", "unowned"])
def test_changed_plan_rejects(
    completed: tuple[Path, Path, dict[str, Any]], tmp_path: Path, monkeypatch: pytest.MonkeyPatch, target: str,
) -> None:
    bundle, source, _ = completed
    plan = tmp_path / "plan"
    build_cpu_diagnostic_plan(bundle, source, output=plan)
    if target == "implementation":
        monkeypatch.setattr(cpu_diagnostic_plan, "_implementation_digest", lambda: "e" * 64)
    elif target == "unowned":
        (plan / "extra.json").write_text("{}")
    else:
        value = json.loads((plan / "diagnostic.json").read_bytes())
        if target == "selection-pin":
            value["references"][0]["source_runs"][0]["selection_digest"] = "0" * 64
        else:
            value["campaign_report_digest"] = "0" * 64
        encoded = json.dumps(value).encode()
        (plan / "diagnostic.json").write_bytes(encoded)
        if target != "bytes":
            lock = json.loads((plan / "bundle.lock.json").read_bytes())
            lock.update(spec_sha256=hashlib.sha256(encoded).hexdigest(), spec_size_bytes=len(encoded))
            (plan / "bundle.lock.json").write_text(json.dumps(lock))
    monkeypatch.setattr(cpu_search, "run_cpu_batch", forbid)
    with pytest.raises(ValueError):
        validate_cpu_diagnostic_plan(plan, campaign_bundle=bundle, campaign_output=source)


def test_incomplete_source_cannot_freeze_a_reference_plan(
    completed: tuple[Path, Path, dict[str, Any]], tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    bundle, _, _ = completed
    source = tmp_path / "interrupted"

    def interrupt(*args: Any, **kwargs: Any) -> Any:
        raise KeyboardInterrupt("incomplete campaign")

    monkeypatch.setattr(cpu_search, "run_cpu_batch", interrupt)
    with pytest.raises(KeyboardInterrupt):
        run_cpu_campaign(bundle, output_dir=source)
    before = files(source)
    monkeypatch.setattr(cpu_search, "run_cpu_batch", forbid)
    with pytest.raises(ValueError, match="every pinned source campaign"):
        build_cpu_diagnostic_plan(bundle, source, output=tmp_path / "plan")
    assert not (tmp_path / "plan").exists() and files(source) == before


def test_plan_requires_fresh_disjoint_paths_without_symlinks(
    completed: tuple[Path, Path, dict[str, Any]], tmp_path: Path,
) -> None:
    bundle, source, _ = completed
    for overlap in (bundle / "diagnostic", source / "diagnostic"):
        with pytest.raises(ValueError, match="overlap"):
            build_cpu_diagnostic_plan(bundle, source, output=overlap)
        assert not overlap.exists()
    plan = tmp_path / "plan"
    build_cpu_diagnostic_plan(bundle, source, output=plan)
    with pytest.raises(ValueError, match="fresh"):
        build_cpu_diagnostic_plan(bundle, source, output=plan)
    alias = tmp_path / "alias"
    alias.symlink_to(plan, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        load_cpu_diagnostic_plan(alias)


def test_plan_inventory_rejects_before_enumerating_an_unbounded_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = tmp_path / "plan"
    plan.mkdir()

    def entries() -> Iterator[SimpleNamespace]:
        for name in ("diagnostic.json", "bundle.lock.json", "unowned.json"):
            yield SimpleNamespace(name=name)
        raise AssertionError("plan loader enumerated beyond its two-asset bound")

    @contextmanager
    def scandir(path: Path) -> Iterator[Iterator[SimpleNamespace]]:
        assert path == plan
        yield entries()

    monkeypatch.setattr(cpu_diagnostic_plan.os, "scandir", scandir)
    with pytest.raises(ValueError, match="unowned"):
        load_cpu_diagnostic_plan(plan)


def test_parent_directory_alias_cannot_publish_inside_source_evidence(tmp_path: Path) -> None:
    bundle, source, sibling = tmp_path / "bundle", tmp_path / "source", tmp_path / "sibling"
    for path in (bundle, source, sibling):
        path.mkdir()
    alias = sibling / ".." / "source" / "diagnostic"
    with pytest.raises(ValueError, match="overlap"):
        build_cpu_diagnostic_plan(bundle, source, output=alias)
    assert not (source / "diagnostic").exists()
