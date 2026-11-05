"""One bounded paired campaign over frozen CPU control tasks.

Eighteen search attempts precede six conditional practice-reference decisions.
Every retained child is verified before later work; a claimed child is never
redispatched, and blocked references consume no execution capacity.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from autocontext.util.file_lock import advisory_path_lock
from autocontext.weight_evolution.artifacts import canonical_digest, file_identity, load_small_json, regular_file
from autocontext.weight_evolution.cpu_campaign import METERS, _bundle_bytes, _derived_bundle, _inventory, _pin
from autocontext.weight_evolution.cpu_campaign_models import CpuCampaignBudget
from autocontext.weight_evolution.cpu_control import _load as _load_control
from autocontext.weight_evolution.cpu_control import _schedule as _control_schedule
from autocontext.weight_evolution.cpu_control_models import CONTROL_FAMILIES
from autocontext.weight_evolution.cpu_control_tasks import (
    CONTROL_REPEATS,
    CONTROL_TASK_SEEDS,
    build_cpu_control_campaign,
)
from autocontext.weight_evolution.cpu_diagnostic import _disjoint, _entries, diagnostic_implementation_digest
from autocontext.weight_evolution.cpu_diagnostic_models import (
    CpuDiagnosticBudget,
    CpuDiagnosticCase,
    CpuDiagnosticReference,
    CpuDiagnosticSourceRun,
    CpuDiagnosticSpec,
)
from autocontext.weight_evolution.cpu_diagnostic_reference import run_cpu_diagnostic_reference
from autocontext.weight_evolution.cpu_execution_campaign_models import (
    EXECUTION_PROFILES,
    CpuExecutionCampaignLock,
    CpuExecutionCampaignSpec,
)
from autocontext.weight_evolution.cpu_execution_models import CpuExecutionContract, CpuExecutionUsage
from autocontext.weight_evolution.cpu_execution_outcomes import execution_runtime_identity
from autocontext.weight_evolution.cpu_execution_reference import (
    report_cpu_execution_reference,
    run_cpu_execution_reference,
)
from autocontext.weight_evolution.cpu_execution_search import report_cpu_execution_search, run_cpu_execution_search
from autocontext.weight_evolution.cpu_failure_prefix import inspect_cpu_reference_prefix, inspect_cpu_search_prefix
from autocontext.weight_evolution.cpu_search import _existing_lock, run_cpu_search, search_implementation_digest
from autocontext.weight_evolution.cpu_search_models import CpuSearchSpec
from autocontext.weight_evolution.session import _STAGING_NAME, _clear_staging, _immutable_json, _read_record, _same

_LOCK = ".cpu-execution-campaign.lock"
_JOURNALS = ("claims", "outcomes", "results")
_STATUSES = ("completed", "failed", "uncertain", "blocked", "not-started")


def _encode(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()


def _zero() -> dict[str, int]:
    return dict.fromkeys(METERS, 0)


def _reference_usage(child: CpuSearchSpec) -> dict[str, int]:
    cases = tuple(case for case in child.cases if case.role == "practice")
    calls = (1 + len(child.options)) * len(cases)
    return {"proposals": 0, "materializations": len(child.candidates), "inference_calls": calls,
            "output_tokens": calls, "input_tokens": (1 + len(child.options)) * sum(len(c.prompt.split()) for c in cases)
            + len(cases) * sum(len((o.context or "").split()) for o in child.options)}


def _budgets(control: Any, templates: dict[str, CpuSearchSpec]) -> tuple[CpuCampaignBudget, CpuDiagnosticBudget]:
    search = CpuCampaignBudget(max_runs=18,
        **{f"max_{meter}": 2 * getattr(control.budget, f"max_{meter}") for meter in METERS})
    reference = CpuDiagnosticBudget(max_references=6, max_proposals=0,
        **{f"max_{meter}": 2 * sum(_reference_usage(t)[meter] for t in templates.values())
           for meter in METERS if meter != "proposals"})
    return search, reference


def build_cpu_execution_campaign(directory: Path, *, campaign_id: str = "cpu-execution-paired-v1") -> CpuExecutionCampaignSpec:
    """Pin the entire fixed search/reference capacity before any inference."""
    _disjoint(directory)
    directory = directory.resolve()
    if directory.exists():
        raise ValueError("paired execution requires a fresh bundle directory")
    control = build_cpu_control_campaign(directory / "campaign", preset="fixture", instances_per_family=1,
        repeats=CONTROL_REPEATS[:1], cases_per_role=4, campaign_id=campaign_id)
    _, templates, _ = _load_control(directory / "campaign")
    runtime = execution_runtime_identity()
    search, reference = _budgets(control, templates)
    identity = file_identity(directory / "campaign" / "bundle.lock.json")
    spec = CpuExecutionCampaignSpec(campaign_id=campaign_id, control_campaign_digest=control.digest,
        control_lock_sha256=identity["sha256"], control_lock_size_bytes=identity["size_bytes"],
        implementation_digest=runtime["implementation_digest"], native_runtime_digest=runtime["native_runtime_digest"],
        measured_profile_digest=runtime["measured_profile_digest"], search_budget=search, reference_budget=reference)
    encoded, runtime_bytes = _encode(spec.to_dict()), _encode(runtime)
    lock = CpuExecutionCampaignLock(spec_sha256=hashlib.sha256(encoded).hexdigest(), spec_size_bytes=len(encoded),
        runtime_sha256=hashlib.sha256(runtime_bytes).hexdigest(), runtime_size_bytes=len(runtime_bytes))
    (directory / "execution.json").write_bytes(encoded)
    (directory / "runtime.json").write_bytes(runtime_bytes)
    (directory / "bundle.lock.json").write_bytes(_encode(lock.to_dict()))
    return spec


def _load(directory: Path) -> tuple[CpuExecutionCampaignSpec, Any, dict[str, CpuSearchSpec], dict[str, Any]]:
    _disjoint(directory)
    if _entries(directory, 4) != {"execution.json", "runtime.json", "bundle.lock.json", "campaign"}:
        raise ValueError("paired execution bundle inventory changed")
    raw_lock = load_small_json(directory / "bundle.lock.json", max_bytes=4096)
    lock = CpuExecutionCampaignLock.model_validate_json(json.dumps(raw_lock))
    _same(raw_lock, lock.to_dict(), "paired execution lock representation changed")
    _pin(directory / "execution.json", lock.spec_sha256, lock.spec_size_bytes)
    _pin(directory / "runtime.json", lock.runtime_sha256, lock.runtime_size_bytes)
    raw = load_small_json(directory / "execution.json")
    spec = CpuExecutionCampaignSpec.model_validate_json(json.dumps(raw))
    _same(raw, spec.to_dict(), "paired execution contract representation changed")
    runtime = load_small_json(directory / "runtime.json", max_bytes=256 * 1024)
    _same(runtime, execution_runtime_identity(), "paired execution runtime or source inventory changed")
    if any(getattr(spec, field) != runtime[field] for field in
           ("implementation_digest", "native_runtime_digest", "measured_profile_digest")):
        raise ValueError("paired execution profile pin changed")
    _pin(directory / "campaign" / "bundle.lock.json", spec.control_lock_sha256, spec.control_lock_size_bytes)
    control, templates, provenance = _load_control(directory / "campaign")
    if (control.digest != spec.control_campaign_digest or control.campaign_id != spec.campaign_id
            or tuple(t.family for t in control.tasks) != CONTROL_FAMILIES
            or tuple(t.generation_seed for t in control.tasks) != tuple(CONTROL_TASK_SEEDS[f][0] for f in CONTROL_FAMILIES)
            or control.repeats != CONTROL_REPEATS[:1]
            or any(p.cases_per_role != 4 for p in provenance.values())
            or _budgets(control, templates) != (spec.search_budget, spec.reference_budget)):
        raise ValueError("paired execution changed its fixed tasks, schedule or full capacity")
    return spec, control, templates, runtime


def _schedule(control: Any, templates: dict[str, CpuSearchSpec]) -> list[dict[str, Any]]:
    family = {task.task_id: task.family for task in control.tasks}
    rows = [{**row, "profile": profile, "stage": "search", "family": family[row["task_id"]]}
            for profile in EXECUTION_PROFILES for row, _ in _control_schedule(control, templates)]
    for profile in EXECUTION_PROFILES:
        for task in control.tasks:
            rows.append({"profile": profile, "stage": "reference", "family": task.family, "task_id": task.task_id,
                "repeat_id": control.repeats[0].repeat_id, "run_id": f"{task.task_id}.{control.repeats[0].repeat_id}",
                "reserved_capacity": _reference_usage(templates[task.task_id])})
    return rows


def validate_cpu_execution_campaign(directory: Path) -> dict[str, Any]:
    spec, control, templates, _ = _load(directory)
    return {"campaign_digest": spec.digest, "profiles": list(EXECUTION_PROFILES), "searches": 18,
            "conditional_references": 6, "schedule": _schedule(control, templates),
            "search_budget": spec.search_budget.to_dict(), "reference_budget": spec.reference_budget.to_dict(),
            "model_execution_performed": False, "gpu_execution_deferred": True, "promotion_eligible": False}


def _contract(spec: CpuExecutionCampaignSpec, *, scope: str, design: str, workflow: str,
              capacity: dict[str, int]) -> CpuExecutionContract:
    return CpuExecutionContract.model_validate_json(json.dumps({"scope": scope, "design_digest": design,
        "workflow_digest": workflow, "workflow_implementation_digest": search_implementation_digest()
            if scope == "search" else diagnostic_implementation_digest(),
        "implementation_digest": spec.implementation_digest, "native_runtime_digest": spec.native_runtime_digest,
        "measured_profile_digest": spec.measured_profile_digest, "failure_contract": spec.failure_contract.to_dict(),
        "capacity": CpuExecutionUsage(**capacity).to_dict()}))


def _entries_clean(path: Path, maximum: int) -> set[str]:
    names = _entries(path, maximum + 16)
    staged = {name for name in names if _STAGING_NAME.fullmatch(name)}
    if len(staged) > 16 or len(names - staged) > maximum:
        raise ValueError("paired execution directory exceeds its entry bound")
    for name in staged:
        file_identity(path / name, max_bytes=1024 * 1024)
    return names - staged


def _preflight(root: Path, manifest: dict[str, Any]) -> None:
    if not root.exists():
        return
    names = _entries_clean(root, 9)
    if names - {_LOCK, "manifest.json", "report.json", "plans", *_JOURNALS, *EXECUTION_PROFILES}:
        raise ValueError("paired execution output contains unowned evidence")
    if "manifest.json" not in names and names - {_LOCK}:
        raise ValueError("paired execution evidence has no manifest")
    if "manifest.json" in names:
        _same(_read_record(root / "manifest.json"), manifest, "paired execution manifest changed")
    if _LOCK in names:
        with regular_file(root / _LOCK):
            pass


def _bundle_state(bundle: Path, expected: dict[str, bytes]) -> bool:
    """Verify partial bootstrap without completing or adopting missing files."""
    if not bundle.exists():
        return False
    names = _entries_clean(bundle, 3)
    if names - set(expected):
        raise ValueError("paired child bundle contains unowned assets")
    for name in names:
        with regular_file(bundle / name) as stream:
            if stream.read(len(expected[name]) + 1) != expected[name]:
                raise ValueError("paired child bundle differs from its schedule")
    return names == set(expected)


def _empty() -> dict[str, Any]:
    return {"status": "absent", "summary": None, "terminal_claim": None, "terminal_result": None}


def _search_prefix(directory: Path, root: Path, row: dict[str, Any], child: CpuSearchSpec,
                   execution: CpuExecutionContract | None) -> dict[str, Any]:
    base = root / row["profile"]
    bundle, output = base / "bundles" / row["run_id"], base / "searches" / row["run_id"]
    complete = _bundle_state(bundle, _bundle_bytes(directory / "campaign", row["task_id"], child))
    if not complete:
        if output.exists():
            raise ValueError("paired child execution has no complete pinned input bundle")
        return _empty()
    if row["profile"] == "legacy-v1":
        return inspect_cpu_search_prefix(bundle, output)
    return report_cpu_execution_search(bundle, output_dir=output, execution=execution)


def _search_projection(summary: dict[str, Any]) -> dict[str, Any]:
    return {key: summary[key] for key in ("study_digest", "runtime_digest", "policy", "selection_digest", "histories",
        "proposal_history", "search_usage", "selected_options", "restoration_verified", "confirmation", "audit_status",
        "reserved_usage", "receipt_digests")}


def _reference_projection(summary: dict[str, Any]) -> dict[str, Any]:
    return {key: summary[key] for key in ("runtime_digest", "scores", "charged_usage", "checkpoint_seals", "receipt_digests")}


def _native_coverage(output: Path, stage: str) -> dict[str, Any]:
    phases: dict[str, dict[str, int]] = {}
    if not (output / "claims").exists():
        return {"profile": "legacy-v1", "inference_operations": 0, "phases": phases, "measured_outcomes": 0}
    for name in sorted(_entries_clean(output / "claims", 256)):
        claim = _read_record(output / "claims" / name)
        if claim.get("kind") != "inference":
            continue
        label = claim.get("label", "unknown")
        phase = ("reference" if stage == "reference" else "restoration" if label.endswith("-restored") else
                 next((p for p in ("confirmation", "audit") if label.startswith(p)), "practice"))
        result_path = output / "results" / name
        result = _read_record(result_path) if result_path.exists() else None
        status = result["status"] if result is not None else "uncertain"
        counts = phases.setdefault(phase, {"completed": 0, "failed": 0, "uncertain": 0})
        counts[status] += 1
    return {"profile": "legacy-v1", "inference_operations": sum(sum(c.values()) for c in phases.values()),
            "phases": phases, "measured_outcomes": 0, "scope": "native-class-only-no-measured-worker-observation"}


def _outcome(row: dict[str, Any], prefix: dict[str, Any], evidence: dict[str, Any],
             *, error_type: str | None = None, authorized: bool = True, output: Path) -> dict[str, Any]:
    child_status = prefix["status"]
    status = ("blocked" if not authorized else "completed" if child_status == "completed" else
              "uncertain" if child_status == "uncertain" else
              "failed" if child_status == "failed" or error_type is not None else "uncertain")
    summary = prefix.get("summary")
    if status == "completed" and not isinstance(summary, dict):
        raise ValueError("completed paired child has no verified summary")
    projection = ((_search_projection if row["stage"] == "search" else _reference_projection)(summary)
                  if status == "completed" and isinstance(summary, dict) else None)
    coverage = (prefix.get("observation_inventory") if row["profile"] == "measured-v2"
                else _native_coverage(output, row["stage"])) if authorized else None
    return {"schema_version": 1, "profile": row["profile"], "stage": row["stage"], "run_id": row["run_id"],
        "status": status, "child_status": child_status, "parent_error_type": error_type, "summary": summary,
        "projection": projection, "observation_coverage": coverage,
        "evidence_digest": canonical_digest(evidence),
        "prefix_digest": canonical_digest(prefix), "historical_failure_cause_recovered": False}


def _plan(spec: CpuExecutionCampaignSpec, control: Any, templates: dict[str, CpuSearchSpec], profile: str,
          source: list[dict[str, Any]]) -> tuple[CpuDiagnosticSpec, dict[str, Any]] | None:
    paired = [entry for entry in source if entry["row"]["profile"] == profile and entry["row"]["stage"] == "search"]
    if len(paired) != 9 or any(entry["outcome"]["status"] != "completed" or not entry["published"] for entry in paired):
        return None
    by_run = {entry["row"]["run_id"]: entry for entry in paired}
    children = {row["run_id"]: child for row, child in _control_schedule(control, templates)}
    refs = []
    repeat = control.repeats[0]
    for task in control.tasks:
        reference_id = f"{task.task_id}.{repeat.repeat_id}"
        run_ids = [f"{reference_id}.{policy}" for policy in control.policies]
        child = children[run_ids[0]]
        practice = tuple(c for c in child.cases if c.role == "practice")
        runs = tuple(CpuDiagnosticSourceRun(run_id=run_id, policy=by_run[run_id]["row"]["policy"],
            study_digest=children[run_id].digest, selection_digest=by_run[run_id]["outcome"]["summary"]["selection_digest"],
            summary_semantic_digest=by_run[run_id]["outcome"]["summary"]["semantic_digest"]) for run_id in run_ids)
        refs.append(CpuDiagnosticReference(reference_id=reference_id, task_id=task.task_id,
            repeat_id=repeat.repeat_id, inference_seed=repeat.inference_seed, source_runs=runs,
            model=child.model, candidates=child.candidates, options=child.options,
            baseline_decoding=child.baseline_decoding, limits=child.limits,
            practice=tuple(CpuDiagnosticCase(case_id=c.case_id, prompt=c.prompt) for c in practice),
            practice_cases_digest=canonical_digest([c.to_dict() for c in practice]),
            source_options_digest=canonical_digest([o.to_dict() for o in child.options])))
    source_report = {"campaign_digest": spec.digest, "profile": profile, "rows": paired}
    ledger = canonical_digest([entry["result"] for entry in paired])
    diagnostic = CpuDiagnosticSpec(diagnostic_id=f"{profile}-practice-reference", campaign_digest=spec.digest,
        runtime_digest=spec.native_runtime_digest, campaign_report_digest=canonical_digest(source_report),
        campaign_ledger_digest=ledger, references=tuple(refs), implementation_digest=spec.implementation_digest,
        budget=CpuDiagnosticBudget(max_references=3, max_proposals=0,
            **{f"max_{m}": getattr(spec.reference_budget, f"max_{m}") // 2 for m in METERS if m != "proposals"}))
    binding = {"campaign_digest": spec.digest, "profile": profile, "source_report_digest": canonical_digest(source_report),
               "source_ledger_digest": ledger, "diagnostic_digest": diagnostic.digest,
               "implementation_digest": spec.implementation_digest}
    return diagnostic, binding


def _reference_args(directory: Path, root: Path, row: dict[str, Any], templates: dict[str, CpuSearchSpec],
                    plan: tuple[CpuDiagnosticSpec, dict[str, Any]]) -> tuple[CpuDiagnosticReference, dict[str, Any]]:
    diagnostic, binding = plan
    ref = next(ref for ref in diagnostic.references if ref.reference_id == row["run_id"])
    source = tuple((root / row["profile"] / "bundles" / run.run_id,
                    root / row["profile"] / "searches" / run.run_id) for run in ref.source_runs)
    args = {"checkpoint_path": source[0][0] / "base.safetensors",
            "materializations": source[0][1] / "materializations",
            "cases": tuple(c for c in templates[row["task_id"]].cases if c.role == "practice"),
            "output_dir": root / row["profile"] / "references" / ref.reference_id,
            "source_searches": source, "source_binding": binding}
    return ref, args


def _reference_prefix(directory: Path, root: Path, row: dict[str, Any], templates: dict[str, CpuSearchSpec],
    plan: tuple[CpuDiagnosticSpec, dict[str, Any]] | None, execution: CpuExecutionContract | None,
) -> dict[str, Any]:
    if plan is None:
        return _empty()
    ref, args = _reference_args(directory, root, row, templates, plan)
    if row["profile"] == "legacy-v1":
        args.pop("source_searches")
        args.pop("source_binding")
        return inspect_cpu_reference_prefix(plan[0], ref, **args)
    return report_cpu_execution_reference(plan[0], ref, execution=execution, **args)


def _claim(spec: CpuExecutionCampaignSpec, row: dict[str, Any], index: int, previous: str,
           plan: tuple[CpuDiagnosticSpec, dict[str, Any]] | None, execution: CpuExecutionContract | None) -> dict[str, Any]:
    authorized = row["stage"] == "search" or plan is not None
    return {"schema_version": 1, "campaign_digest": spec.digest, "index": index, "row": row,
            "previous_result_digest": previous, "dispatch_authorized": authorized,
            "claimed_capacity": row["reserved_capacity"] if authorized else _zero(),
            "reference_plan_digest": canonical_digest({"diagnostic": plan[0].to_dict(), "binding": plan[1]})
                if plan is not None else None,
            "execution": execution.to_dict() if execution is not None else None}


def _result(claim: dict[str, Any], outcome: dict[str, Any]) -> dict[str, Any]:
    summary = outcome["summary"]
    usage = None
    if outcome["status"] == "completed":
        usage = summary["reserved_usage"] if claim["row"]["stage"] == "search" else summary["charged_usage"]
    return {"schema_version": 1, "claim_digest": canonical_digest(claim), "outcome_digest": canonical_digest(outcome),
            "status": outcome["status"], "charged_capacity": claim["claimed_capacity"], "verified_usage": usage}


def _inspect(directory: Path, root: Path, spec: CpuExecutionCampaignSpec, control: Any,
             templates: dict[str, CpuSearchSpec]) -> list[dict[str, Any]]:
    names = {folder: _entries_clean(root / folder, 24) if (root / folder).exists() else set() for folder in _JOURNALS}
    claims, outcomes, results = (names[folder] for folder in _JOURNALS)
    if (claims != {f"{i:08d}.json" for i in range(len(claims))}
            or results != {f"{i:08d}.json" for i in range(len(results))}
            or not outcomes <= claims or not results <= claims or len(claims) - len(results) > 1):
        raise ValueError("paired execution journal is not a contiguous fixed schedule prefix")
    if (root / "report.json").exists() and len(results) != 24:
        raise ValueError("sealed paired report is missing terminal decisions")
    schedule = _schedule(control, templates)
    children = {row["run_id"]: child for row, child in _control_schedule(control, templates)}
    for profile in EXECUTION_PROFILES:
        base = root / profile
        if base.exists() and _entries_clean(base, 3) - {"bundles", "searches", "references"}:
            raise ValueError("paired execution profile has unowned evidence")
        for folder, stage in (("bundles", "search"), ("searches", "search"), ("references", "reference")):
            path = base / folder
            allowed = {row["run_id"] for row in schedule[:len(claims)] if row["profile"] == profile and row["stage"] == stage}
            if path.exists() and _entries_clean(path, 18) - allowed:
                raise ValueError("paired execution retains an unclaimed child")
    prefix: list[dict[str, Any]] = []
    previous = "0" * 64
    for index, row in enumerate(schedule[:len(claims)]):
        name = f"{index:08d}.json"
        plan = _plan(spec, control, templates, row["profile"], prefix[:18]) if row["stage"] == "reference" else None
        child = children[row["run_id"]] if row["stage"] == "search" else None
        execution = (_contract(spec, scope="search", design=child.digest, workflow=child.digest,
                              capacity=child.required_usage) if child is not None else
                     _contract(spec, scope="reference", design=next(r.digest for r in plan[0].references
                        if r.reference_id == row["run_id"]), workflow=plan[0].digest, capacity=row["reserved_capacity"])
                     if plan is not None else None)
        if row["profile"] == "legacy-v1":
            execution = None
        claim = _claim(spec, row, index, previous, plan, execution)
        _same(_read_record(root / "claims" / name), claim, "paired parent claim changed")
        output = root / row["profile"] / ("searches" if child else "references") / row["run_id"]
        if not claim["dispatch_authorized"] and output.exists():
            raise ValueError("blocked reference has unauthorized child evidence")
        child_prefix = (_search_prefix(directory, root, row, child, execution) if child is not None else
                        _reference_prefix(directory, root, row, templates, plan, execution))
        saved = _read_record(root / "outcomes" / name) if name in outcomes else None
        error_type = saved.get("parent_error_type") if saved else None
        if error_type is not None and (type(error_type) is not str or not 1 <= len(error_type) <= 256):
            raise ValueError("invalid bounded parent exception class")
        evidence = {"child": _inventory(output)}
        if child is not None:
            evidence["bundle"] = _inventory(root / row["profile"] / "bundles" / row["run_id"])
        outcome = _outcome(row, child_prefix, evidence, error_type=error_type,
                           authorized=claim["dispatch_authorized"], output=output)
        if saved is not None:
            _same(saved, outcome, "paired parent outcome differs from verified child evidence")
        result = _result(claim, outcome)
        published = name in results
        if published:
            if saved is None:
                raise ValueError("paired result is missing its immutable outcome")
            _same(_read_record(root / "results" / name), result, "paired parent result changed")
        prefix.append({"row": row, "claim": claim, "outcome": outcome, "result": result,
                       "published": published, "outcome_published": saved is not None})
        previous = canonical_digest(result)
    plans = _entries_clean(root / "plans", 2) if (root / "plans").exists() else set()
    if plans - {f"{p}.json" for p in EXECUTION_PROFILES}:
        raise ValueError("paired execution has an unknown reference plan")
    for name in plans:
        plan = _plan(spec, control, templates, name[:-5], prefix[:18])
        if plan is None:
            raise ValueError("reference plan has incomplete source selections")
        _same(_read_record(root / "plans" / name), {"diagnostic": plan[0].to_dict(), "binding": plan[1]},
              "paired reference plan or complete source binding changed")
    for entry in prefix[18:]:
        if entry["claim"]["dispatch_authorized"] and f"{entry['row']['profile']}.json" not in plans:
            raise ValueError("claimed reference is missing its frozen plan")
    return prefix


def _report(spec: CpuExecutionCampaignSpec, schedule: list[dict[str, Any]], prefix: list[dict[str, Any]]) -> dict[str, Any]:
    rows = []
    for index, row in enumerate(schedule):
        entry = prefix[index] if index < len(prefix) else None
        outcome = entry["outcome"] if entry else None
        rows.append({**row, "status": outcome["status"] if outcome else "not-started",
            "result_published": entry["published"] if entry else False,
            "charged_capacity": entry["claim"]["claimed_capacity"] if entry else _zero(),
            "verified_usage": entry["result"]["verified_usage"] if entry else None,
            "projection": outcome["projection"] if outcome else None,
            "observation_coverage": outcome["observation_coverage"] if outcome else None,
            "parent_error_type": outcome["parent_error_type"] if outcome else None})
    pairs = []
    for left in rows[:9] + rows[18:21]:
        right = next(r for r in rows if r["profile"] == "measured-v2" and r["stage"] == left["stage"]
                     and r["run_id"] == left["run_id"])
        available = left["status"] == right["status"] == "completed"
        components = {key: canonical_digest(value) == canonical_digest(right["projection"][key])
                      for key, value in left["projection"].items()} if available else None
        pairs.append({"stage": left["stage"], "run_id": left["run_id"], "family": left["family"],
                      "legacy_status": left["status"], "measured_status": right["status"],
                      "available": available, "matches": all(components.values()) if components is not None else None,
                      "components": components})
    counts = {stage: {status: sum(r["stage"] == stage and r["status"] == status for r in rows) for status in _STATUSES}
              for stage in ("search", "reference")}
    family_counts = {family: {profile: {stage: {status: sum(r["family"] == family and r["profile"] == profile
        and r["stage"] == stage and r["status"] == status for r in rows) for status in _STATUSES}
        for stage in ("search", "reference")} for profile in EXECUTION_PROFILES} for family in CONTROL_FAMILIES}
    report = {"schema_version": 1, "report_kind": "paired-cpu-execution", "campaign_digest": spec.digest,
        "rows": rows, "pairs": pairs, "status_counts": counts, "family_status_counts": family_counts,
        "search_budget": spec.search_budget.to_dict(), "reference_budget": spec.reference_budget.to_dict(),
        "charged_capacity": {stage: {m: sum(r["charged_capacity"][m] for r in rows if r["stage"] == stage)
            for m in METERS} for stage in ("search", "reference")},
        "completed_verified_usage": {stage: {m: sum((r["verified_usage"] or _zero())[m] for r in rows if r["stage"] == stage)
            for m in METERS} for stage in ("search", "reference")},
        "unknown_usage_rows": {stage: sum(r["stage"] == stage and r["status"] in {"failed", "uncertain"}
            for r in rows) for stage in ("search", "reference")},
        "pending_results": sum(not entry["published"] for entry in prefix),
        "acceptance_passed": len(prefix) == 24 and all(entry["published"] for entry in prefix)
            and all(pair["matches"] is True for pair in pairs),
        "ledger_digest": canonical_digest(prefix), "gpu_execution_deferred": True, "promotion_eligible": False,
        "historical_failure_cause_recovered": False, "family_pooling_performed": False,
        "scope": "fixed-cpu-execution-parity-not-adaptive-advantage-or-qwen-reproduction"}
    return {**report, "semantic_digest": canonical_digest(report)}


def _dispatch(directory: Path, root: Path, row: dict[str, Any], child: CpuSearchSpec | None,
    templates: dict[str, CpuSearchSpec], plan: tuple[CpuDiagnosticSpec, dict[str, Any]] | None,
    execution: CpuExecutionContract | None,
) -> None:
    if child is not None:
        base = root / row["profile"]
        bundle = base / "bundles" / row["run_id"]
        _derived_bundle(bundle, _bundle_bytes(directory / "campaign", row["task_id"], child), write=True)
        if row["profile"] == "legacy-v1":
            run_cpu_search(bundle, output_dir=base / "searches" / row["run_id"])
        else:
            run_cpu_execution_search(bundle, output_dir=base / "searches" / row["run_id"], execution=execution)
    elif plan is not None:
        ref, args = _reference_args(directory, root, row, templates, plan)
        if row["profile"] == "legacy-v1":
            args.pop("source_searches")
            args.pop("source_binding")
            run_cpu_diagnostic_reference(plan[0], ref, **args)
        else:
            run_cpu_execution_reference(plan[0], ref, execution=execution, **args)


def _run(directory: Path, *, output_dir: Path, execute: bool) -> dict[str, Any]:
    _disjoint(directory, output_dir)
    directory, root = directory.resolve(), output_dir.resolve()
    spec, control, templates, runtime = _load(directory)
    schedule = _schedule(control, templates)
    manifest = {"schema_version": 1, "spec": spec.to_dict(), "runtime": runtime, "schedule": schedule}
    _preflight(root, manifest)
    if execute:
        root.mkdir(parents=True, exist_ok=True)
    with advisory_path_lock(root / _LOCK) if execute else _existing_lock(root / _LOCK):
        _preflight(root, manifest)
        if not (root / "manifest.json").exists():
            if not execute:
                raise ValueError("paired execution manifest missing")
            _immutable_json(root / "manifest.json", manifest)
        prefix = _inspect(directory, root, spec, control, templates)
        if execute:
            for path in (root, *(root / n for n in (*_JOURNALS, "plans")),
                         *(root / p / n for p in EXECUTION_PROFILES for n in ("bundles", "searches", "references"))):
                path.mkdir(parents=True, exist_ok=True)
                _clear_staging(path)
            children = {row["run_id"]: child for row, child in _control_schedule(control, templates)}
            for index, row in enumerate(schedule):
                # Re-prove every retained child and parent record before later
                # work, including mutations made during a preceding dispatch.
                prefix = _inspect(directory, root, spec, control, templates)
                name = f"{index:08d}.json"
                if index < len(prefix):
                    entry = prefix[index]
                    if not entry["outcome_published"]:
                        _immutable_json(root / "outcomes" / name, entry["outcome"])
                    if not entry["published"]:
                        _immutable_json(root / "results" / name, entry["result"])
                        entry["published"] = True
                        entry["outcome_published"] = True
                    continue
                _load(directory)
                plan = _plan(spec, control, templates, row["profile"], prefix[:18]) if row["stage"] == "reference" else None
                child = children[row["run_id"]] if row["stage"] == "search" else None
                execution = (_contract(spec, scope="search", design=child.digest, workflow=child.digest,
                                      capacity=child.required_usage) if child is not None else
                    _contract(spec, scope="reference", design=next(r.digest for r in plan[0].references
                        if r.reference_id == row["run_id"]), workflow=plan[0].digest, capacity=row["reserved_capacity"])
                    if plan is not None else None)
                if row["profile"] == "legacy-v1":
                    execution = None
                if plan is not None:
                    _immutable_json(root / "plans" / f"{row['profile']}.json",
                                    {"diagnostic": plan[0].to_dict(), "binding": plan[1]})
                previous = canonical_digest(prefix[-1]["result"]) if prefix else "0" * 64
                claim = _claim(spec, row, index, previous, plan, execution)
                _immutable_json(root / "claims" / name, claim)
                error_type = None
                if claim["dispatch_authorized"]:
                    try:
                        _dispatch(directory, root, row, child, templates, plan, execution)
                    except Exception as error:
                        error_type = type(error).__name__[:256]
                child_prefix = (_search_prefix(directory, root, row, child, execution) if child is not None else
                                _reference_prefix(directory, root, row, templates, plan, execution))
                output = root / row["profile"] / ("searches" if child else "references") / row["run_id"]
                evidence = {"child": _inventory(output)}
                if child is not None:
                    evidence["bundle"] = _inventory(root / row["profile"] / "bundles" / row["run_id"])
                outcome = _outcome(row, child_prefix, evidence, error_type=error_type,
                                   authorized=claim["dispatch_authorized"], output=output)
                _immutable_json(root / "outcomes" / name, outcome)
                _load(directory)
                result = _result(claim, outcome)
                _immutable_json(root / "results" / name, result)
                prefix.append({"row": row, "claim": claim, "outcome": outcome, "result": result,
                               "published": True, "outcome_published": True})
            prefix = _inspect(directory, root, spec, control, templates)
        report = _report(spec, schedule, prefix)
        if (root / "report.json").exists():
            _same(_read_record(root / "report.json"), report, "paired execution report changed")
        elif execute:
            _immutable_json(root / "report.json", report)
        return report


def run_cpu_execution_campaign(directory: Path, *, output_dir: Path) -> dict[str, Any]:
    return _run(directory, output_dir=output_dir, execute=True)


def report_cpu_execution_campaign(directory: Path, *, output_dir: Path) -> dict[str, Any]:
    return _run(directory, output_dir=output_dir, execute=False)
