"""Measured execution of the unchanged native CPU search policy and gates."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from autocontext.util.file_lock import advisory_path_lock
from autocontext.weight_evolution import cpu_search as native
from autocontext.weight_evolution.artifacts import canonical_digest, regular_file
from autocontext.weight_evolution.checkpoint_candidates import TensorCandidateRegistry
from autocontext.weight_evolution.cpu_campaign import _inventory
from autocontext.weight_evolution.cpu_execution_journal import (
    DIRECTORIES,
    METERS,
    CpuExecutionError,
    ExecutionJournal,
    _ExecutionBlocked,
    _NeedOperation,
    entries,
    is_staging,
    prepare,
)
from autocontext.weight_evolution.cpu_execution_models import CpuExecutionContract, CpuExecutionUsage
from autocontext.weight_evolution.cpu_execution_outcomes import build_cpu_execution_contract
from autocontext.weight_evolution.session import _immutable_json, _read_record, _same


def _disjoint(root: Path, *sources: Path) -> None:
    for path in (root, *sources):
        native._safe(path.absolute())
    resolved = root.resolve()
    if any(resolved == source.resolve() or resolved.is_relative_to(source.resolve())
           or source.resolve().is_relative_to(resolved) for source in sources):
        raise CpuExecutionError("measured execution output overlaps its immutable inputs")


def _projection_inventory(root: Path, *, search: bool) -> set[str]:
    if not root.exists():
        return set()
    allowed = {"complete.json", "selection.json", "confirmation.json", "seals", "reports"} if search else {"complete.json"}
    names = entries(root, len(allowed))
    if names - allowed:
        raise CpuExecutionError("native projection contains unowned artifacts")
    files: set[str] = set()
    for name in names:
        path = root / name
        if name in {"seals", "reports"}:
            for child in entries(path, 16):
                with regular_file(path / child):
                    pass
                files.add(f"{name}/{child}")
        else:
            with regular_file(path):
                pass
            files.add(name)
    return files


def _bootstrap_empty(root: Path, manifest: dict[str, Any]) -> bool:
    if not root.exists():
        return True
    names = entries(root, 9)
    if names - {".cpu-execution.lock", "manifest.json", "projection", *DIRECTORIES}:
        return False
    for name in names:
        path = root / name
        if name == "manifest.json":
            _same(_read_record(path), manifest, "measured execution bootstrap changed")
        elif name == ".cpu-execution.lock":
            with regular_file(path):
                pass
        elif entries(path, 5 if name == "projection" else 256):
            return False
    return True


def _empty_report(contract: CpuExecutionContract) -> dict[str, Any]:
    return {"schema_version": 1, "report_kind": "measured_cpu_execution", "scope": contract.scope,
            "contract": contract.to_dict(), "contract_digest": contract.digest, "status": "absent", "summary": None,
            "charged_usage": {meter: 0 for meter in METERS}, "reserved_capacity": contract.capacity.to_dict(),
            "terminal_claim": None, "terminal_result": None, "terminal_outcome": None,
            "observation_inventory": [], "reconciliation_needed": False, "blocked": False,
            "model_execution_performed": False, "gpu_execution_deferred": True, "promotion_eligible": False,
            "benchmark_result_claimed": False, "inventory": {}, "ledger_digest": canonical_digest({})}


def _report(root: Path, journal: ExecutionJournal, summary: dict[str, Any] | None,
            *, blocked: bool, search: bool, write: bool) -> dict[str, Any]:
    if journal.index + int(blocked) != journal.available:
        raise CpuExecutionError("execution retains claims beyond its verified workflow prefix")
    projection = _projection_inventory(root / "projection", search=search)
    expected = journal.seen_artifacts if search else ({"complete.json"} if summary is not None else set())
    if projection - expected:
        raise CpuExecutionError("execution projection extends beyond its verified prefix")
    if journal.was_complete and (summary is None or journal.reconciliation_needed):
        raise CpuExecutionError("completed execution projection is missing durable operation evidence")
    if summary is not None and "complete.json" not in projection:
        journal.reconciliation_needed = True
    inventory = {name: value for name, value in _inventory(root).items()
                 if name != "report.json" and not any(is_staging(part) for part in Path(name).parts)}
    status = journal.status if blocked else ("completed" if summary is not None else "uncertain")
    if journal.reconciliation_needed:
        status = "uncertain"
    known_execution = any(row["process_observation"] is not None and row["process_observation"]["dispatched"]
                          for row in journal.observation_inventory)
    unknown_execution = any(row["kind"] == "inference" and not row["outcome_present"]
                            for row in journal.observation_inventory)
    report = {"schema_version": 1, "report_kind": "measured_cpu_execution", "scope": journal.contract.scope,
              "contract": journal.contract.to_dict(), "contract_digest": journal.contract.digest,
              "status": status, "summary": summary if status == "completed" else None,
              "charged_usage": journal.spent, "reserved_capacity": journal.contract.capacity.to_dict(),
              "terminal_claim": journal.terminal_claim, "terminal_result": journal.terminal_result,
              "terminal_outcome": journal.terminal_outcome, "observation_inventory": journal.observation_inventory,
              "reconciliation_needed": journal.reconciliation_needed, "blocked": blocked,
              "model_execution_performed": True if known_execution else None if unknown_execution else False,
              "gpu_execution_deferred": True, "promotion_eligible": False, "benchmark_result_claimed": False,
              "inventory": inventory, "ledger_digest": canonical_digest(inventory)}
    path = root / "report.json"
    if path.exists():
        _same(_read_record(path), report, "measured execution report differs from verified evidence")
    elif write and status in {"completed", "failed"}:
        _immutable_json(path, report)
    return report


def _materialization_inventory(root: Path, journal: ExecutionJournal) -> None:
    store = root / "materializations"
    if not store.exists():
        return
    candidates = {candidate.digest for candidate in journal.registry.candidates if candidate.program is not None}
    claimed = journal.materialized & candidates
    terminal = journal.terminal_claim
    if terminal is not None and terminal["kind"] == "materialize":
        claimed.add(canonical_digest(terminal["input"]["candidate"]))
    claimed &= candidates
    allowed = {".tensor-candidates.lock", *claimed, *(f".{digest}.tensor-edit.lock" for digest in claimed)}
    if entries(store, 32) - allowed:
        raise CpuExecutionError("execution retains an unclaimed checkpoint materialization")


def _run(directory: Path, *, output_dir: Path, execution: CpuExecutionContract | None,
         write: bool) -> dict[str, Any]:
    _disjoint(output_dir, directory)
    native.validate_cpu_search(directory)
    spec = native.load_cpu_search(directory)
    expected = build_cpu_execution_contract(
        scope="search", design_digest=spec.digest, workflow_digest=spec.digest,
        workflow_implementation_digest=spec.implementation_digest,
        capacity=CpuExecutionUsage(**spec.required_usage),
        failure_contract=execution.failure_contract if execution is not None else None,
    )
    contract = execution or expected
    _same(contract.to_dict(), expected.to_dict(), "search execution contract differs from current source/runtime/design pins")
    root = output_dir.resolve()
    manifest = {"schema_version": 1, "kind": "measured-cpu-search-journal-v1", "execution": contract.to_dict(),
                "contract_digest": contract.digest, "native_study": spec.to_dict(), "native_projection": "projection"}
    _inventory(root)  # Bounds traversal before any listing, publication, or cleanup.
    if not write and _bootstrap_empty(root, manifest):
        return _empty_report(contract)
    if write:
        root.mkdir(parents=True, exist_ok=True)
    lock = root / ".cpu-execution.lock"
    if lock.exists() or lock.is_symlink():
        with regular_file(lock):
            pass
    with advisory_path_lock(lock) if write else native._existing_lock(lock):
        prepare(root, manifest, write=write, operation_count=len(spec.candidates) + 6 * spec.rounds + 17, search=True)
        projection = root / "projection"
        if write:
            for name in ("seals", "reports"):
                (projection / name).mkdir(exist_ok=True)
        elif not all((projection / name).is_dir() for name in ("seals", "reports")):
            raise CpuExecutionError("search projection directories are missing")
        read_registry = native._ReadOnlyRegistry(spec.candidates, input_path=directory / "base.safetensors",
                                                  output_dir=root / "materializations")

        def replay(registry: TensorCandidateRegistry, *, readonly: bool) -> tuple[ExecutionJournal, dict[str, Any] | None, bool]:
            journal = ExecutionJournal(spec, root, contract, registry, replay=readonly)
            summary = None
            blocked = False
            try:
                summary = native._workflow(cast(native._Journal, journal), registry)
            except _NeedOperation:
                if journal.was_complete:
                    raise CpuExecutionError("completed search is missing an operation") from None
            except _ExecutionBlocked:
                blocked = True
            _materialization_inventory(root, journal)
            _report(root, journal, summary, blocked=blocked, search=True, write=False)
            return journal, summary, blocked

        journal, summary, blocked = replay(read_registry, readonly=True)
        if write and not (summary is not None and journal.was_complete) and not (
            blocked and journal.terminal_outcome is None
        ):
            registry = TensorCandidateRegistry(spec.candidates, input_path=directory / "base.safetensors",
                                               output_dir=root / "materializations")
            journal, summary, blocked = replay(registry, readonly=False)
        return _report(root, journal, summary, blocked=blocked, search=True, write=write)


def run_cpu_execution_search(native_bundle: Path, *, output_dir: Path,
                             execution: CpuExecutionContract | None = None) -> dict[str, Any]:
    """Resume only unclaimed work or deterministic publication of saved outcomes."""
    return _run(native_bundle, output_dir=output_dir, execution=execution, write=True)


def report_cpu_execution_search(native_bundle: Path, *, output_dir: Path,
                                execution: CpuExecutionContract | None = None) -> dict[str, Any]:
    """Validate completed, failed, or uncertain prefixes without writes or execution."""
    return _run(native_bundle, output_dir=output_dir, execution=execution, write=False)
