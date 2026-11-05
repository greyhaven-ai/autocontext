"""Measured exhaustive references over complete, verified source search stores."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from autocontext.util.file_lock import advisory_path_lock
from autocontext.weight_evolution import cpu_diagnostic_reference as native
from autocontext.weight_evolution.artifacts import canonical_digest, regular_file
from autocontext.weight_evolution.cpu_campaign import _inventory
from autocontext.weight_evolution.cpu_diagnostic import diagnostic_implementation_digest
from autocontext.weight_evolution.cpu_diagnostic_models import CpuDiagnosticReference, CpuDiagnosticSpec
from autocontext.weight_evolution.cpu_execution_journal import (
    CpuExecutionError,
    ExecutionJournal,
    _ExecutionBlocked,
    _NeedOperation,
    prepare,
)
from autocontext.weight_evolution.cpu_execution_models import CpuExecutionContract, CpuExecutionUsage
from autocontext.weight_evolution.cpu_execution_outcomes import build_cpu_execution_contract
from autocontext.weight_evolution.cpu_execution_search import (
    _bootstrap_empty,
    _disjoint,
    _empty_report,
    _report,
    report_cpu_execution_search,
)
from autocontext.weight_evolution.cpu_failure_prefix import inspect_cpu_search_prefix
from autocontext.weight_evolution.cpu_search import _existing_lock, _ReadOnlyRegistry, load_cpu_search
from autocontext.weight_evolution.cpu_study import CpuCase
from autocontext.weight_evolution.session import _read_record, _same


def _sources(reference: CpuDiagnosticReference, source_searches: tuple[tuple[Path, Path], ...],
             *, checkpoint_path: Path, materializations: Path) -> dict[str, Any]:
    if type(source_searches) is not tuple or len(source_searches) != 3:
        raise CpuExecutionError("reference requires all three complete source search locations in pinned order")
    if (checkpoint_path.resolve() != (source_searches[0][0] / "base.safetensors").resolve()
            or materializations.resolve() != (source_searches[0][1] / "materializations").resolve()):
        raise CpuExecutionError("reference checkpoint/store differs from its first pinned source search")
    proofs = []
    for binding, (bundle, output) in zip(reference.source_runs, source_searches, strict=True):
        study = load_cpu_search(bundle)
        manifest = _read_record(output / "manifest.json")
        measured = manifest.get("kind") == "measured-cpu-search-journal-v1"
        report = (report_cpu_execution_search(bundle, output_dir=output) if measured
                  else inspect_cpu_search_prefix(bundle, output))
        summary = report["summary"]
        if report["status"] != "completed" or summary is None:
            raise CpuExecutionError("exhaustive reference requires every source search to be complete and verified")
        expected = {"study_digest": binding.study_digest, "selection_digest": binding.selection_digest,
                    "semantic_digest": binding.summary_semantic_digest}
        if any(summary.get(key) != value for key, value in expected.items()) or study.digest != binding.study_digest:
            raise CpuExecutionError("reference source summary differs from the pinned diagnostic selection")
        practice = tuple(case for case in study.cases if case.role == "practice")
        if (study.policy.name != binding.policy or study.model != reference.model
                or study.candidates != reference.candidates or study.options != reference.options
                or study.baseline_decoding != reference.baseline_decoding or study.limits != reference.limits
                or canonical_digest([case.to_dict() for case in practice]) != reference.practice_cases_digest):
            raise CpuExecutionError("reference design differs from the completed source search")
        proofs.append({"run_id": binding.run_id, "profile": "measured" if measured else "native",
                       "study_digest": study.digest, "summary_semantic_digest": summary["semantic_digest"],
                       "selection_digest": summary["selection_digest"], "report_digest": canonical_digest(report),
                       "source_inventory_digest": canonical_digest(_inventory(output))})
    return {"complete_source_searches": proofs}


def _run(spec: CpuDiagnosticSpec, reference: CpuDiagnosticReference, *, checkpoint_path: Path,
         materializations: Path, cases: tuple[CpuCase, ...], output_dir: Path,
         source_searches: tuple[tuple[Path, Path], ...], execution: CpuExecutionContract | None,
         source_binding: dict[str, Any] | None, write: bool) -> dict[str, Any]:
    source_paths = tuple(path for pair in source_searches for path in pair)
    _disjoint(output_dir, checkpoint_path, materializations, *source_paths)
    if (reference not in spec.references or any(case.role != "practice" for case in cases)
            or canonical_digest([case.to_dict() for case in cases]) != reference.practice_cases_digest
            or [(case.case_id, case.prompt) for case in cases]
            != [(case.case_id, case.prompt) for case in reference.practice]):
        raise CpuExecutionError("measured reference requires exactly the frozen practice cases")
    expected = build_cpu_execution_contract(
        scope="reference", design_digest=reference.digest, workflow_digest=spec.digest,
        workflow_implementation_digest=diagnostic_implementation_digest(),
        capacity=CpuExecutionUsage(**reference.required_usage),
        failure_contract=execution.failure_contract if execution is not None else None,
    )
    contract = execution or expected
    _same(contract.to_dict(), expected.to_dict(), "reference contract differs from current source/runtime/design pins")
    if contract.native_runtime_digest != spec.runtime_digest:
        raise CpuExecutionError("reference native runtime differs from its frozen source")
    source_proof = _sources(reference, source_searches, checkpoint_path=checkpoint_path, materializations=materializations)
    if source_binding is not None:
        if type(source_binding) is not dict or len(json.dumps(source_binding, allow_nan=False).encode()) > 65536:
            raise CpuExecutionError("reference parent source binding exceeds its bounded object contract")
        source_proof["parent_binding"] = source_binding
    registry = _ReadOnlyRegistry(reference.candidates, input_path=checkpoint_path, output_dir=materializations)
    registry.verify_existing()
    root = output_dir.resolve()
    manifest = {"schema_version": 1, "kind": "measured-cpu-reference-journal-v1", "execution": contract.to_dict(),
                "contract_digest": contract.digest, "diagnostic_digest": spec.digest,
                "reference": reference.to_dict(), "practice_cases_digest": reference.practice_cases_digest,
                "verified_source": source_proof, "native_projection": "projection"}
    _inventory(root)
    if not write and _bootstrap_empty(root, manifest):
        return _empty_report(contract)
    if write:
        root.mkdir(parents=True, exist_ok=True)
    lock = root / ".cpu-execution.lock"
    if lock.exists() or lock.is_symlink():
        with regular_file(lock):
            pass
    with advisory_path_lock(lock) if write else _existing_lock(lock):
        prepare(root, manifest, write=write,
                operation_count=len(reference.candidates) + len(reference.options) + 1, search=False)

        def replay(*, readonly: bool) -> tuple[ExecutionJournal, dict[str, Any] | None, bool]:
            journal = ExecutionJournal(spec, root, contract, registry, replay=readonly, reference=reference)
            summary = None
            blocked = False
            try:
                # The frozen workflow writes complete.json directly; journal.root
                # is the explicitly labeled projection directory, never this ledger.
                summary = native._workflow(cast(native._Journal, journal), registry, cases)
            except _NeedOperation:
                if journal.was_complete:
                    raise CpuExecutionError("completed reference is missing an operation") from None
            except _ExecutionBlocked:
                blocked = True
            _report(root, journal, summary, blocked=blocked, search=False, write=False)
            return journal, summary, blocked

        journal, summary, blocked = replay(readonly=True)
        if write and not (summary is not None and journal.was_complete) and not (
            blocked and journal.terminal_outcome is None
        ):
            journal, summary, blocked = replay(readonly=False)
        return _report(root, journal, summary, blocked=blocked, search=False, write=write)


def run_cpu_execution_reference(
    spec: CpuDiagnosticSpec, reference: CpuDiagnosticReference, *, checkpoint_path: Path,
    materializations: Path, cases: tuple[CpuCase, ...], output_dir: Path,
    source_searches: tuple[tuple[Path, Path], ...], execution: CpuExecutionContract | None = None,
    source_binding: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return _run(spec, reference, checkpoint_path=checkpoint_path, materializations=materializations,
                cases=cases, output_dir=output_dir, source_searches=source_searches,
                execution=execution, source_binding=source_binding, write=True)


def report_cpu_execution_reference(
    spec: CpuDiagnosticSpec, reference: CpuDiagnosticReference, *, checkpoint_path: Path,
    materializations: Path, cases: tuple[CpuCase, ...], output_dir: Path,
    source_searches: tuple[tuple[Path, Path], ...], execution: CpuExecutionContract | None = None,
    source_binding: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return _run(spec, reference, checkpoint_path=checkpoint_path, materializations=materializations,
                cases=cases, output_dir=output_dir, source_searches=source_searches,
                execution=execution, source_binding=source_binding, write=False)
