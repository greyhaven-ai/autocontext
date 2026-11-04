"""Read-only verification of native search prefixes, including failed work.

The original completed verifier is reused unchanged. Incomplete work is replayed
through the same pure workflow and read-only registry without repairing it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from autocontext.weight_evolution.artifacts import canonical_digest, regular_file
from autocontext.weight_evolution.cpu_campaign import _inventory, _safe
from autocontext.weight_evolution.cpu_diagnostic import _entries
from autocontext.weight_evolution.cpu_diagnostic_models import CpuDiagnosticReference, CpuDiagnosticSpec
from autocontext.weight_evolution.cpu_diagnostic_reference import (
    CpuDiagnosticReferenceBlockedError,
    CpuDiagnosticReferenceError,
    verify_cpu_diagnostic_reference,
)
from autocontext.weight_evolution.cpu_diagnostic_reference import _Journal as _ReferenceJournal
from autocontext.weight_evolution.cpu_diagnostic_reference import _NeedExecution as _ReferenceNeedExecution
from autocontext.weight_evolution.cpu_diagnostic_reference import _prepare as _prepare_reference
from autocontext.weight_evolution.cpu_diagnostic_reference import _workflow as _reference_workflow
from autocontext.weight_evolution.cpu_runner import runtime_identity
from autocontext.weight_evolution.cpu_search import (
    CpuSearchBlockedError,
    CpuSearchError,
    _artifact_inventory,
    _existing_lock,
    _Journal,
    _NeedExecution,
    _ReadOnlyRegistry,
    _workflow,
    load_cpu_search,
    validate_cpu_search,
    verify_cpu_search,
)
from autocontext.weight_evolution.cpu_study import CpuCase
from autocontext.weight_evolution.session import _read_record, _same


def inspect_cpu_search_prefix(bundle: Path, root: Path) -> dict[str, Any]:
    """Verify all available native evidence; never dispatch or backfill work."""
    _safe(bundle.absolute())
    _safe(root.absolute())
    if root.resolve().is_relative_to(bundle.resolve()) or bundle.resolve().is_relative_to(root.resolve()):
        raise CpuSearchError("observed search output overlaps its source bundle")
    validate_cpu_search(bundle)
    spec = load_cpu_search(bundle)
    _inventory(root)
    empty = {"status": "absent", "summary": None, "terminal_claim": None, "terminal_result": None}
    if not root.exists():
        return empty
    names = _entries(root, 16)
    allowed = {".cpu-search.lock", "manifest.json", "materializations", "claims", "results", "seals", "reports",
               "selection.json", "confirmation.json", "complete.json"}
    if names - allowed:
        raise CpuSearchError("observed search contains unowned evidence")
    if "manifest.json" not in names:
        # A process can stop while creating its empty native bootstrap folders.
        for name in names:
            path = root / name
            if name == ".cpu-search.lock":
                with regular_file(path):
                    pass
            elif name not in {"claims", "results", "seals", "reports"} or _entries(path, 0):
                raise CpuSearchError("observed search has evidence without its manifest")
        return empty
    if not {".cpu-search.lock", "claims", "results", "seals", "reports"} <= names:
        raise CpuSearchError("observed search is missing native prefix directories")
    with _existing_lock(root / ".cpu-search.lock"):
        runtime = runtime_identity()
        _same(_read_record(root / "manifest.json"), {
            "schema_version": 1, "study": spec.to_dict(), "study_digest": spec.digest,
            "runtime": runtime, "runtime_digest": canonical_digest(runtime),
        }, "observed native manifest or runtime changed")
        claims, results = _entries(root / "claims", 256), _entries(root / "results", 256)
        if (claims != {f"{i:08d}.json" for i in range(len(claims))}
                or results != {f"{i:08d}.json" for i in range(len(results))}
                or not results <= claims or len(claims) - len(results) > 1):
            raise CpuSearchError("observed native journal is not a contiguous bounded prefix")
        last = f"{len(claims) - 1:08d}.json"
        claim = _read_record(root / "claims" / last) if claims else None
        result = _read_record(root / "results" / last) if last in results else None
        if "complete.json" in names:
            # Release the outer read lock before the original verifier acquires it.
            complete = True
        else:
            complete = False
            registry = _ReadOnlyRegistry(spec.candidates, input_path=bundle / "base.safetensors",
                                         output_dir=root / "materializations")
            replay = _Journal(spec, root, canonical_digest(runtime), replay=True)
            blocked = False
            try:
                _workflow(replay, registry)
            except CpuSearchBlockedError:
                blocked = True
                if replay.index + 1 != len(claims):
                    raise CpuSearchError("observed search retains claims beyond a blocked operation") from None
            except _NeedExecution:
                if replay.index != len(claims):
                    raise CpuSearchError("observed search prefix ended before its retained claims") from None
            if _artifact_inventory(root) - replay.seen_artifacts:
                raise CpuSearchError("observed search has artifacts beyond its verified prefix")
            claimed_candidates = set(replay.materialized)
            if blocked and claim is not None and claim["kind"] == "materialize":
                claimed_candidates.add(canonical_digest(claim["input"]["candidate"]))
            if (root / "materializations").exists():
                allowed_store = {".tensor-candidates.lock", *claimed_candidates,
                                 *(f".{digest}.tensor-edit.lock" for digest in claimed_candidates)}
                if _entries(root / "materializations", 32) - allowed_store:
                    raise CpuSearchError("observed search retains an unclaimed materialization")
    if complete:
        return {"status": "completed", "summary": verify_cpu_search(bundle, output_dir=root),
                "terminal_claim": claim, "terminal_result": result}
    return {"status": "failed" if result is not None and result.get("status") == "failed" else "uncertain",
            "summary": None, "terminal_claim": claim, "terminal_result": result}


def inspect_cpu_reference_prefix(
    spec: CpuDiagnosticSpec, reference: CpuDiagnosticReference, *, checkpoint_path: Path,
    materializations: Path, cases: tuple[CpuCase, ...], output_dir: Path,
) -> dict[str, Any]:
    """Verify a native reference prefix without creating locks or dispatching work."""
    if (reference not in spec.references
            or canonical_digest([case.to_dict() for case in cases]) != reference.practice_cases_digest
            or any(case.role != "practice" for case in cases)
            or [(case.case_id, case.prompt) for case in cases]
            != [(case.case_id, case.prompt) for case in reference.practice]):
        raise CpuDiagnosticReferenceError("observed reference requires exactly its pinned practice cases")
    if canonical_digest(runtime_identity()) != spec.runtime_digest:
        raise CpuDiagnosticReferenceError("observed reference runtime differs from its frozen source")
    for path in (output_dir, checkpoint_path, materializations):
        _safe(path.absolute())
    root = output_dir.resolve()
    if any(root == source or root.is_relative_to(source) or source.is_relative_to(root)
           for source in (checkpoint_path.resolve(), materializations.resolve())):
        raise CpuDiagnosticReferenceError("observed reference output overlaps source artifacts")
    registry = _ReadOnlyRegistry(reference.candidates, input_path=checkpoint_path, output_dir=materializations)
    registry.verify_existing()
    _inventory(root)
    empty = {"status": "absent", "summary": None, "terminal_claim": None, "terminal_result": None}
    if not root.exists():
        return empty
    names = _entries(root, 8)
    allowed = {".cpu-reference.lock", "manifest.json", "claims", "results", "complete.json"}
    if names - allowed:
        raise CpuDiagnosticReferenceError("observed reference contains unowned evidence")
    if "manifest.json" not in names:
        for name in names:
            path = root / name
            if name == ".cpu-reference.lock":
                with regular_file(path):
                    pass
            elif name not in {"claims", "results"} or _entries(path, 0):
                raise CpuDiagnosticReferenceError("observed reference has evidence without its manifest")
        return empty
    if not {".cpu-reference.lock", "claims", "results"} <= names:
        raise CpuDiagnosticReferenceError("observed reference is missing native prefix directories")
    count = len(reference.candidates) + len(reference.options) + 1
    manifest = {"schema_version": 1, "diagnostic_digest": spec.digest, "reference_digest": reference.digest,
                "runtime_digest": spec.runtime_digest, "practice_cases_digest": reference.practice_cases_digest}
    with _existing_lock(root / ".cpu-reference.lock"):
        _prepare_reference(root, manifest, write=False, operation_count=count)
        claims, results = _entries(root / "claims", count), _entries(root / "results", count)
        last = f"{len(claims) - 1:08d}.json"
        claim = _read_record(root / "claims" / last) if claims else None
        result = _read_record(root / "results" / last) if last in results else None
        complete = "complete.json" in names
        if not complete:
            replay = _ReferenceJournal(spec, reference, root, replay=True)
            try:
                _reference_workflow(replay, registry, cases)
            except CpuDiagnosticReferenceBlockedError:
                if replay.index + 1 != len(claims):
                    raise CpuDiagnosticReferenceError("observed reference has claims beyond a blocked operation") from None
            except _ReferenceNeedExecution:
                if replay.index != len(claims):
                    raise CpuDiagnosticReferenceError("observed reference ended before its retained claims") from None
    if complete:
        summary = verify_cpu_diagnostic_reference(spec, reference, checkpoint_path=checkpoint_path,
                    materializations=materializations, cases=cases, output_dir=root)
        if result is None or summary["journal_digest"] != canonical_digest(result):
            raise CpuDiagnosticReferenceError("observed reference terminal evidence changed during verification")
        return {"status": "completed", "summary": summary, "terminal_claim": claim, "terminal_result": result}
    return {"status": "failed" if result is not None and result.get("status") == "failed" else "uncertain",
            "summary": None, "terminal_claim": claim, "terminal_result": result}
