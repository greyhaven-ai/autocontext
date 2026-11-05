from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from test_weight_cpu_diagnostic_models import spec_for
from test_weight_cpu_execution_search import failed_measurement
from test_weight_cpu_search import builder, files, forbid, rehash

from autocontext.weight_evolution import cpu_execution_journal as journal
from autocontext.weight_evolution import cpu_execution_reference as reference_module
from autocontext.weight_evolution.artifacts import canonical_digest
from autocontext.weight_evolution.checkpoint_candidates import TensorCandidateRegistry
from autocontext.weight_evolution.cpu_campaign_models import CAMPAIGN_POLICIES
from autocontext.weight_evolution.cpu_diagnostic import diagnostic_implementation_digest
from autocontext.weight_evolution.cpu_diagnostic_models import CpuDiagnosticCase, CpuDiagnosticReference, CpuDiagnosticSourceRun
from autocontext.weight_evolution.cpu_execution_models import CpuExecutionUsage
from autocontext.weight_evolution.cpu_execution_outcomes import build_cpu_execution_contract
from autocontext.weight_evolution.cpu_runner import runtime_identity
from autocontext.weight_evolution.cpu_search import load_cpu_search
from autocontext.weight_evolution.cpu_search_models import CpuSearchPolicy
from autocontext.weight_evolution.session import _read_record


def make_reference(child: Any, studies: list[Any] | None = None) -> CpuDiagnosticReference:
    practice = tuple(case for case in child.cases if case.role == "practice")
    return CpuDiagnosticReference(reference_id="task-01.r0", task_id="task-01", repeat_id="r0",
        inference_seed=child.evaluation_seed,
        source_runs=tuple(CpuDiagnosticSourceRun(run_id=f"task-01.r0.{policy}", policy=policy,
            study_digest=studies[index].digest if studies is not None else "1" * 64,
            selection_digest="2" * 64, summary_semantic_digest="3" * 64) for index, policy in enumerate(CAMPAIGN_POLICIES)),
        model=child.model, candidates=child.candidates, options=child.options, baseline_decoding=child.baseline_decoding,
        limits=child.limits, practice=tuple(CpuDiagnosticCase(case_id=c.case_id, prompt=c.prompt) for c in practice),
        practice_cases_digest=canonical_digest([case.to_dict() for case in practice]),
        source_options_digest=canonical_digest([option.to_dict() for option in child.options]))


@pytest.fixture
def context(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Any, ...]:
    bundle, source, output = tmp_path / "bundle", tmp_path / "source", tmp_path / "output"
    builder().build_bundle(bundle)
    child = load_cpu_search(bundle)
    registry = TensorCandidateRegistry(child.candidates, input_path=bundle / "base.safetensors",
                                       output_dir=source / "materializations")
    for candidate in child.candidates:
        registry.materialize(candidate.handle)
    reference = make_reference(child)
    spec = spec_for(reference).model_copy(update={"runtime_digest": canonical_digest(runtime_identity())})
    execution = build_cpu_execution_contract(scope="reference", design_digest=reference.digest, workflow_digest=spec.digest,
        workflow_implementation_digest=diagnostic_implementation_digest(), capacity=CpuExecutionUsage(**reference.required_usage))
    monkeypatch.setattr(reference_module, "build_cpu_execution_contract", lambda **kwargs: execution)
    monkeypatch.setattr(journal.ExecutionJournal, "_current", lambda self: None)
    monkeypatch.setattr(reference_module, "_sources", lambda *a, **k: {"test_only_complete_source": "fixed"})
    calls: list[Any] = []

    def measured(requests: Any, **kwargs: Any) -> Any:
        calls.append(requests)
        return failed_measurement(requests, execution)

    monkeypatch.setattr(journal, "run_measured_cpu_batch", measured)
    kwargs = {"checkpoint_path": bundle / "base.safetensors", "materializations": source / "materializations",
              "cases": tuple(case for case in child.cases if case.role == "practice"), "output_dir": output,
              "source_searches": ((bundle, source),) * 3, "execution": execution,
              "source_binding": {"campaign_report_digest": "a" * 64}}
    return spec, reference, kwargs, calls, source


def test_reference_keeps_failure_charged_and_only_probes_practice(context: Any) -> None:
    spec, reference, kwargs, calls, source = context
    before = files(source)
    report = reference_module.run_cpu_execution_reference(spec, reference, **kwargs)
    assert report["status"] == "failed" and len(calls) == 1 and files(source) == before
    assert report["charged_usage"]["materializations"] == len(reference.candidates)
    assert report["charged_usage"]["proposals"] == 0
    assert report["reserved_capacity"] == reference.required_usage
    assert report["terminal_claim"]["label"] == "A"
    assert all(row["phase"] == "reference" for row in report["observation_inventory"])
    assert all(request.prompt == case.prompt for request, case in zip(calls[0], reference.practice, strict=True))
    assert not (kwargs["output_dir"] / "materializations").exists()


def test_reference_readonly_and_resume_preserve_source_and_retained_evidence(context: Any,
                                                                           monkeypatch: pytest.MonkeyPatch) -> None:
    spec, reference, kwargs, _, source = context
    report = reference_module.run_cpu_execution_reference(spec, reference, **kwargs)
    before = files(source), files(kwargs["output_dir"])
    monkeypatch.setattr(journal, "run_measured_cpu_batch", forbid)
    monkeypatch.setattr(journal, "_immutable_json", forbid)
    monkeypatch.setattr(TensorCandidateRegistry, "materialize", forbid)
    assert reference_module.report_cpu_execution_reference(spec, reference, **kwargs) == report
    assert reference_module.run_cpu_execution_reference(spec, reference, **kwargs) == report
    assert (files(source), files(kwargs["output_dir"])) == before


def test_reference_saved_outcome_repairs_result_without_repeating_inference(context: Any,
                                                                          monkeypatch: pytest.MonkeyPatch) -> None:
    spec, reference, kwargs, calls, _ = context
    original = journal._immutable_json

    def stop(path: Path, value: dict[str, Any]) -> None:
        original(path, value)
        if path.parent.name == "outcomes" and value["operation_kind"] == "inference":
            raise KeyboardInterrupt

    with monkeypatch.context() as patch:
        patch.setattr(journal, "_immutable_json", stop)
        with pytest.raises(KeyboardInterrupt):
            reference_module.run_cpu_execution_reference(spec, reference, **kwargs)
    before = files(kwargs["output_dir"])
    monkeypatch.setattr(journal, "run_measured_cpu_batch", forbid)
    report = reference_module.report_cpu_execution_reference(spec, reference, **kwargs)
    assert report["status"] == "uncertain" and report["terminal_result"] is None
    assert files(kwargs["output_dir"]) == before
    assert reference_module.run_cpu_execution_reference(spec, reference, **kwargs)["status"] == "failed"
    assert len(calls) == 1


@pytest.mark.parametrize("changed", ["cases", "source_binding", "source_checkpoint", "outcome"])
def test_reference_tampering_cannot_reach_another_probe(context: Any, changed: str) -> None:
    spec, reference, kwargs, calls, _ = context
    reference_module.run_cpu_execution_reference(spec, reference, **kwargs)
    if changed == "cases":
        kwargs["cases"] = (kwargs["cases"][0].model_copy(update={"role": "audit"}), *kwargs["cases"][1:])
    elif changed == "source_binding":
        kwargs["source_binding"] = {"campaign_report_digest": "b" * 64}
    elif changed == "source_checkpoint":
        path = kwargs["checkpoint_path"]
        path.write_bytes(path.read_bytes() + b"corrupt")
    else:
        path = kwargs["output_dir"] / "outcomes" / "00000000.json"
        value = _read_record(path)
        value["contract_digest"] = "0" * 64
        rehash(path, value)
    with pytest.raises(ValueError):
        reference_module.report_cpu_execution_reference(spec, reference, **kwargs)
    assert len(calls) == 1


@pytest.mark.parametrize("measured", [False, True])
@pytest.mark.parametrize("status", ["completed", "failed", "uncertain"])
def test_source_verification_requires_all_three_complete_bound_searches(tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch, measured: bool, status: str,
) -> None:
    bundle = tmp_path / "bundle"
    builder().build_bundle(bundle)
    child = load_cpu_search(bundle)
    studies = [child.model_copy(update={"policy": CpuSearchPolicy(
                   name=policy, order_seed=7 if policy == "seeded-random-v1" else 0)})
               for policy in CAMPAIGN_POLICIES]
    reference = make_reference(child, studies)
    pairs = tuple((tmp_path / f"bundle-{i}", tmp_path / f"output-{i}") for i in range(3))
    monkeypatch.setattr(reference_module, "load_cpu_search", lambda path: studies[int(path.name[-1])])
    monkeypatch.setattr(reference_module, "_read_record",
                        lambda path: {"kind": "measured-cpu-search-journal-v1"} if measured else {})
    calls: list[int] = []

    def report(path: Path, *args: Any, **kwargs: Any) -> dict[str, Any]:
        index = int(path.name[-1])
        calls.append(index)
        actual_status = status if index == 1 else "completed"
        summary = {"study_digest": studies[index].digest, "selection_digest": "2" * 64, "semantic_digest": "3" * 64}
        return {"status": actual_status, "summary": summary if actual_status == "completed" else None}

    monkeypatch.setattr(reference_module, "report_cpu_execution_search", report)
    monkeypatch.setattr(reference_module, "inspect_cpu_search_prefix", report)
    args = {"checkpoint_path": pairs[0][0] / "base.safetensors", "materializations": pairs[0][1] / "materializations"}
    if status == "completed":
        proof = reference_module._sources(reference, pairs, **args)
        assert len(proof["complete_source_searches"]) == 3 and calls == [0, 1, 2]
    else:
        with pytest.raises(ValueError, match="every source search"):
            reference_module._sources(reference, pairs, **args)
        assert calls == [0, 1]


@pytest.fixture(scope="module")
def real_reference(tmp_path_factory: pytest.TempPathFactory) -> tuple[Any, ...]:
    import json
    import shutil

    from test_weight_cpu_search import repin

    from autocontext.weight_evolution.cpu_campaign_tasks import build_cpu_task
    from autocontext.weight_evolution.cpu_execution_search import run_cpu_execution_search

    root = tmp_path_factory.mktemp("measured-reference-real")
    first = root / "bundle-0"
    build_cpu_task(first, task_id="task-01", generation_seed=31, cases_per_role=2)
    pairs = []
    studies = []
    reports = []
    for index, policy in enumerate(CAMPAIGN_POLICIES):
        bundle = first if index == 0 else Path(shutil.copytree(first, root / f"bundle-{index}"))
        raw = json.loads((bundle / "study.json").read_bytes())
        raw["policy"] = CpuSearchPolicy(name=policy, order_seed=7 if policy == "seeded-random-v1" else 0).to_dict()
        repin(bundle, raw)
        output = root / f"source-{index}"
        reports.append(run_cpu_execution_search(bundle, output_dir=output))
        assert reports[-1]["status"] == "completed"
        studies.append(load_cpu_search(bundle))
        pairs.append((bundle, output))
    reference = make_reference(studies[0], studies)
    bindings = tuple(CpuDiagnosticSourceRun(run_id=binding.run_id, policy=binding.policy,
        study_digest=binding.study_digest, selection_digest=report["summary"]["selection_digest"],
        summary_semantic_digest=report["summary"]["semantic_digest"])
        for binding, report in zip(reference.source_runs, reports, strict=True))
    reference = reference.model_copy(update={"source_runs": bindings})
    spec = spec_for(reference).model_copy(update={"runtime_digest": canonical_digest(runtime_identity())})
    kwargs = {"checkpoint_path": first / "base.safetensors", "materializations": pairs[0][1] / "materializations",
              "cases": tuple(case for case in studies[0].cases if case.role == "practice"),
              "output_dir": root / "reference", "source_searches": tuple(pairs)}
    before = [files(path) for pair in pairs for path in pair]
    report = reference_module.run_cpu_execution_reference(spec, reference, **kwargs)
    assert [files(path) for pair in pairs for path in pair] == before
    return spec, reference, kwargs, report


def test_real_reference_exhausts_practice_over_verified_complete_sources(real_reference: Any) -> None:
    _, reference, kwargs, report = real_reference
    assert report["status"] == "completed" and report["model_execution_performed"]
    assert report["charged_usage"] == reference.required_usage
    assert set(report["summary"]["scores"]) == {"A", *(option.option_id for option in reference.options)}
    assert len([row for row in report["observation_inventory"] if row["kind"] == "inference"]) == 13
    assert all(row["phase"] == "reference" for row in report["observation_inventory"])
    assert (kwargs["output_dir"] / "projection" / "complete.json").exists()
    assert not (kwargs["output_dir"] / "complete.json").exists()


def test_real_reference_readonly_replay_and_resume_do_no_work(real_reference: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    spec, reference, kwargs, report = real_reference
    before = files(kwargs["output_dir"])
    monkeypatch.setattr(journal, "run_measured_cpu_batch", forbid)
    monkeypatch.setattr(journal, "_immutable_json", forbid)
    monkeypatch.setattr(TensorCandidateRegistry, "materialize", forbid)
    assert reference_module.report_cpu_execution_reference(spec, reference, **kwargs) == report
    assert reference_module.run_cpu_execution_reference(spec, reference, **kwargs) == report
    assert files(kwargs["output_dir"]) == before
