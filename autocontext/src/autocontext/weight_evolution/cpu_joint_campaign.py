"""M18 joint-control campaign preserving every declared search/reference slot.

The M16 child adapters remain unchanged. This parent ledger binds the full
18-search roster before its six conditional, practice-only references. A claim
is never redispatched; incomplete observations remain uncertain and charged.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from autocontext.util.file_lock import advisory_path_lock
from autocontext.weight_evolution.artifacts import canonical_digest, file_identity, regular_file
from autocontext.weight_evolution.cpu_campaign import _bundle_bytes, _derived_bundle, _inventory
from autocontext.weight_evolution.cpu_diagnostic import _disjoint, _entries, diagnostic_implementation_digest
from autocontext.weight_evolution.cpu_diagnostic_models import (
    CpuDiagnosticCase,
    CpuDiagnosticReference,
    CpuDiagnosticSourceRun,
    CpuDiagnosticSpec,
)
from autocontext.weight_evolution.cpu_execution_campaign import (
    _outcome,
    _reference_args,
    _reference_prefix,
    _result,
    _search_prefix,
    _zero,
)
from autocontext.weight_evolution.cpu_execution_models import CpuExecutionContract, CpuExecutionUsage
from autocontext.weight_evolution.cpu_execution_reference import run_cpu_execution_reference
from autocontext.weight_evolution.cpu_execution_search import run_cpu_execution_search
from autocontext.weight_evolution.cpu_joint_campaign_bundle import joint_campaign_schedule, load_cpu_joint_campaign_bundle
from autocontext.weight_evolution.cpu_joint_campaign_models import CpuJointCampaignSpec
from autocontext.weight_evolution.cpu_joint_campaign_report import build_cpu_joint_campaign_report
from autocontext.weight_evolution.cpu_joint_control_models import CpuJointControlCampaignSpec
from autocontext.weight_evolution.cpu_joint_control_tasks import joint_control_schedule as _control_schedule
from autocontext.weight_evolution.cpu_search import _existing_lock, search_implementation_digest
from autocontext.weight_evolution.cpu_search_models import CpuSearchSpec
from autocontext.weight_evolution.session import (
    _STAGING_NAME,
    MAX_JOURNAL_RECORD_BYTES,
    _clear_staging,
    _immutable_json,
    _read_record,
    _same,
)

_LOCK = ".cpu-joint-campaign.lock"
_PROFILE = "measured-v2"
_JOURNALS = ("claims", "outcomes", "results")
_SEARCHES = 18
_REFERENCES = 6
_SLOTS = _SEARCHES + _REFERENCES
_Plan = tuple[CpuDiagnosticSpec, dict[str, Any]]


def _entries_clean(path: Path, maximum: int) -> set[str]:
    """Bound publication scratch by the writer's limit without adopting it.

    Full pilot reports can exceed M16's one-MiB inventory scratch limit. The
    journal writer and cleanup permit sixteen MiB, so recovery must accept the
    same bounded temporary files, including partially written JSON.
    """
    names = _entries(path, maximum + 16)
    staged = {name for name in names if _STAGING_NAME.fullmatch(name)}
    if len(staged) > 16 or len(names - staged) > maximum:
        raise ValueError("measured joint campaign directory exceeds its entry bound")
    for name in staged:
        file_identity(path / name, max_bytes=MAX_JOURNAL_RECORD_BYTES)
    return names - staged


def _preflight(root: Path, manifest: dict[str, Any]) -> None:
    if not root.exists():
        return
    names = _entries_clean(root, 8)
    if names - {_LOCK, "manifest.json", "report.json", "plans", *_JOURNALS, _PROFILE}:
        raise ValueError("measured joint campaign output contains unowned evidence")
    if "manifest.json" not in names and names - {_LOCK}:
        raise ValueError("measured joint campaign evidence has no manifest")
    if "manifest.json" in names:
        _same(_read_record(root / "manifest.json"), manifest, "measured joint campaign manifest changed")
    if _LOCK in names:
        with regular_file(root / _LOCK):
            pass


def _contract(spec: CpuJointCampaignSpec, *, scope: str, design: str, workflow: str,
              capacity: dict[str, int]) -> CpuExecutionContract:
    # The pilot coordinator's source identity must never replace M16's adapter
    # identity in a child execution contract.
    return CpuExecutionContract.model_validate_json(json.dumps({"scope": scope, "design_digest": design,
        "workflow_digest": workflow, "workflow_implementation_digest": search_implementation_digest()
            if scope == "search" else diagnostic_implementation_digest(),
        "implementation_digest": spec.execution_implementation_digest,
        "native_runtime_digest": spec.native_runtime_digest, "measured_profile_digest": spec.measured_profile_digest,
        "failure_contract": spec.failure_contract.to_dict(), "capacity": CpuExecutionUsage(**capacity).to_dict()}))


def _plan(spec: CpuJointCampaignSpec, control: CpuJointControlCampaignSpec, templates: dict[str, CpuSearchSpec],
          source: list[dict[str, Any]], *, children: dict[str, CpuSearchSpec] | None = None) -> _Plan | None:
    if (len(source) != _SEARCHES or any(entry["row"]["stage"] != "search"
            or entry["row"]["profile"] != _PROFILE or entry["outcome"]["status"] != "completed"
            or not entry["published"] for entry in source)):
        return None
    by_run = {entry["row"]["run_id"]: entry for entry in source}
    if children is None:
        children = {row["run_id"]: child for row, child in _control_schedule(control, templates)}
    if set(by_run) != set(children):
        raise ValueError("measured joint campaign reference source roster differs")
    refs = []
    for task in control.tasks:
        for repeat in control.repeats:
            reference_id = f"{task.task_id}.{repeat.repeat_id}"
            run_ids = [f"{reference_id}.{policy}" for policy in control.policies]
            child = children[run_ids[0]]
            practice = tuple(c for c in child.cases if c.role == "practice")
            runs = tuple(CpuDiagnosticSourceRun(run_id=run_id, policy=by_run[run_id]["row"]["policy"],
                study_digest=children[run_id].digest,
                selection_digest=by_run[run_id]["outcome"]["summary"]["selection_digest"],
                summary_semantic_digest=by_run[run_id]["outcome"]["summary"]["semantic_digest"]) for run_id in run_ids)
            refs.append(CpuDiagnosticReference(reference_id=reference_id, task_id=task.task_id,
                repeat_id=repeat.repeat_id, inference_seed=repeat.inference_seed, source_runs=runs,
                model=child.model, candidates=child.candidates, options=child.options,
                baseline_decoding=child.baseline_decoding, limits=child.limits,
                practice=tuple(CpuDiagnosticCase(case_id=c.case_id, prompt=c.prompt) for c in practice),
                practice_cases_digest=canonical_digest([c.to_dict() for c in practice]),
                source_options_digest=canonical_digest([o.to_dict() for o in child.options])))
    source_report = {"campaign_digest": spec.digest, "profile": _PROFILE, "rows": source}
    ledger = canonical_digest([entry["result"] for entry in source])
    diagnostic = CpuDiagnosticSpec(diagnostic_id="joint-campaign-practice-reference-v1", campaign_digest=spec.digest,
        runtime_digest=spec.native_runtime_digest, campaign_report_digest=canonical_digest(source_report),
        campaign_ledger_digest=ledger, references=tuple(refs), implementation_digest=spec.implementation_digest,
        budget=spec.reference_budget)
    binding = {"campaign_digest": spec.digest, "profile": _PROFILE,
               "source_report_digest": diagnostic.campaign_report_digest, "source_ledger_digest": ledger,
               "source_search_count": _SEARCHES, "diagnostic_digest": diagnostic.digest,
               "implementation_digest": spec.implementation_digest}
    return diagnostic, binding


def _execution(spec: CpuJointCampaignSpec, row: dict[str, Any], child: CpuSearchSpec | None,
               plan: _Plan | None) -> CpuExecutionContract | None:
    if child is not None:
        return _contract(spec, scope="search", design=child.digest, workflow=child.digest, capacity=child.required_usage)
    if plan is not None:
        reference = next(ref for ref in plan[0].references if ref.reference_id == row["run_id"])
        if reference.required_usage != row["reserved_capacity"]:
            raise ValueError("measured joint campaign reference capacity differs from its declared slot")
        return _contract(spec, scope="reference", design=reference.digest, workflow=plan[0].digest,
                         capacity=row["reserved_capacity"])
    return None


def _claim(spec: CpuJointCampaignSpec, row: dict[str, Any], index: int, previous: str,
           plan: _Plan | None, execution: CpuExecutionContract | None) -> dict[str, Any]:
    authorized = row["stage"] == "search" or plan is not None
    return {"schema_version": 1, "campaign_digest": spec.digest, "index": index, "row": row,
            "previous_result_digest": previous, "dispatch_authorized": authorized,
            "claimed_capacity": row["reserved_capacity"] if authorized else _zero(),
            "reference_plan_digest": canonical_digest({"diagnostic": plan[0].to_dict(), "binding": plan[1]})
                if plan is not None else None,
            "execution": execution.to_dict() if execution is not None else None}


def _details(output: Path, outcome: dict[str, Any]) -> dict[str, Any] | None:
    """Read only projections already replay-verified by the child adapter."""
    if outcome["status"] != "completed":
        return None
    projection = output / "projection"
    return {"selection": _read_record(projection / "selection.json"),
            "practice": _read_record(projection / "reports" / "practice.json"),
            "confirmation": _read_record(projection / "reports" / "confirmation.json"),
            "audit": _read_record(projection / "reports" / "audit.json")
                if outcome["summary"]["audit_status"] == "completed" else None}


def _inspect(directory: Path, root: Path, spec: CpuJointCampaignSpec, control: CpuJointControlCampaignSpec,
             templates: dict[str, CpuSearchSpec], *, schedule: list[dict[str, Any]] | None = None,
             children: dict[str, CpuSearchSpec] | None = None) -> list[dict[str, Any]]:
    names = {folder: _entries_clean(root / folder, _SLOTS) if (root / folder).exists() else set()
             for folder in _JOURNALS}
    claims, outcomes, results = (names[folder] for folder in _JOURNALS)
    if (claims != {f"{i:08d}.json" for i in range(len(claims))}
            or results != {f"{i:08d}.json" for i in range(len(results))}
            or not outcomes <= claims or not results <= claims or len(claims) - len(results) > 1):
        raise ValueError("measured joint campaign journal is not a contiguous fixed schedule prefix")
    if (root / "report.json").exists() and len(results) != _SLOTS:
        raise ValueError("sealed measured joint campaign report is missing terminal decisions")
    if schedule is None:
        schedule = joint_campaign_schedule(control, templates)
    if children is None:
        children = {row["run_id"]: child for row, child in _control_schedule(control, templates)}
    base = root / _PROFILE
    if base.exists() and _entries_clean(base, 3) - {"bundles", "searches", "references"}:
        raise ValueError("measured joint campaign profile has unowned evidence")
    for folder, stage, maximum in (("bundles", "search", _SEARCHES), ("searches", "search", _SEARCHES),
                                    ("references", "reference", _REFERENCES)):
        path = base / folder
        allowed = {row["run_id"] for row in schedule[:len(claims)] if row["stage"] == stage}
        if path.exists() and _entries_clean(path, maximum) - allowed:
            raise ValueError("measured joint campaign retains an unclaimed child")
    prefix: list[dict[str, Any]] = []
    previous = "0" * 64
    plan: _Plan | None = None
    for index, row in enumerate(schedule[:len(claims)]):
        name = f"{index:08d}.json"
        if index == _SEARCHES:
            plan = _plan(spec, control, templates, prefix, children=children)
        child = children[row["run_id"]] if row["stage"] == "search" else None
        execution = _execution(spec, row, child, plan)
        claim = _claim(spec, row, index, previous, plan, execution)
        _same(_read_record(root / "claims" / name), claim, "measured joint campaign parent claim changed")
        output = base / ("searches" if child is not None else "references") / row["run_id"]
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
            evidence["bundle"] = _inventory(base / "bundles" / row["run_id"])
        outcome = _outcome(row, child_prefix, evidence, error_type=error_type,
                           authorized=claim["dispatch_authorized"], output=output)
        if saved is not None:
            _same(saved, outcome, "measured joint campaign parent outcome differs from verified child evidence")
        result = _result(claim, outcome)
        published = name in results
        if published:
            if saved is None:
                raise ValueError("measured joint campaign result is missing its immutable outcome")
            _same(_read_record(root / "results" / name), result, "measured joint campaign parent result changed")
        prefix.append({"row": row, "claim": claim, "outcome": outcome, "result": result,
                       "published": published, "outcome_published": saved is not None,
                       "details": _details(output, outcome) if child is not None else None,
                       "reference": next(ref.to_dict() for ref in plan[0].references
                           if ref.reference_id == row["run_id"]) if child is None and plan is not None else None})
        previous = canonical_digest(result)
    plans = _entries_clean(root / "plans", 1) if (root / "plans").exists() else set()
    if plans - {f"{_PROFILE}.json"}:
        raise ValueError("measured joint campaign has an unknown reference plan")
    if plans:
        # A plan may be published immediately before its first parent claim.
        if plan is None:
            plan = _plan(spec, control, templates, prefix[:_SEARCHES], children=children)
        if plan is None:
            raise ValueError("reference plan has incomplete source selections")
        _same(_read_record(root / "plans" / f"{_PROFILE}.json"),
              {"diagnostic": plan[0].to_dict(), "binding": plan[1]},
              "measured joint campaign reference plan or complete source binding changed")
    if any(entry["claim"]["dispatch_authorized"] for entry in prefix[_SEARCHES:]) and not plans:
        raise ValueError("claimed reference is missing its frozen plan")
    return prefix


def _dispatch(directory: Path, root: Path, row: dict[str, Any], child: CpuSearchSpec | None,
              templates: dict[str, CpuSearchSpec], plan: _Plan | None,
              execution: CpuExecutionContract | None) -> None:
    if child is not None:
        bundle = root / _PROFILE / "bundles" / row["run_id"]
        _derived_bundle(bundle, _bundle_bytes(directory / "campaign", row["task_id"], child), write=True)
        run_cpu_execution_search(bundle, output_dir=root / _PROFILE / "searches" / row["run_id"], execution=execution)
    elif plan is not None:
        ref, args = _reference_args(directory, root, row, templates, plan)
        run_cpu_execution_reference(plan[0], ref, execution=execution, **args)


def _report(spec: CpuJointCampaignSpec, control: CpuJointControlCampaignSpec, templates: dict[str, CpuSearchSpec],
            schedule: list[dict[str, Any]], prefix: list[dict[str, Any]]) -> dict[str, Any]:
    return build_cpu_joint_campaign_report(spec, control, templates, schedule, prefix)


def _run(directory: Path, *, output_dir: Path, execute: bool) -> dict[str, Any]:
    _disjoint(directory, output_dir)
    directory, root = directory.resolve(), output_dir.resolve()
    spec, control, templates, runtime = load_cpu_joint_campaign_bundle(directory)
    schedule = joint_campaign_schedule(control, templates)
    children = {row["run_id"]: child for row, child in _control_schedule(control, templates)}
    manifest = {"schema_version": 1, "kind": "measured-cpu-joint-campaign-journal-v1",
                "spec": spec.to_dict(), "runtime": runtime, "schedule": schedule}
    _preflight(root, manifest)

    # Completed replay takes only an existing read lock and verifies everything
    # once. It must not create directories, clear staging or republish any file.
    if not execute or (root / "report.json").exists():
        if not (root / "manifest.json").exists():
            raise ValueError("measured joint campaign manifest missing")
        with _existing_lock(root / _LOCK):
            _preflight(root, manifest)
            if not (root / "manifest.json").exists():
                raise ValueError("measured joint campaign manifest missing")
            prefix = _inspect(directory, root, spec, control, templates, schedule=schedule, children=children)
            report = _report(spec, control, templates, schedule, prefix)
            if (root / "report.json").exists():
                _same(_read_record(root / "report.json"), report, "measured joint campaign report changed")
                return report
            if not execute:
                return report

    root.mkdir(parents=True, exist_ok=True)
    with advisory_path_lock(root / _LOCK):
        _preflight(root, manifest)
        if not (root / "manifest.json").exists():
            _immutable_json(root / "manifest.json", manifest)
        prefix = _inspect(directory, root, spec, control, templates, schedule=schedule, children=children)
        for path in (root, *(root / n for n in (*_JOURNALS, "plans")),
                     *(root / _PROFILE / n for n in ("bundles", "searches", "references"))):
            path.mkdir(parents=True, exist_ok=True)
            _clear_staging(path)
        for index, row in enumerate(schedule):
            if index < len(prefix) and prefix[index]["published"]:
                continue
            # Verify every retained result and child again before new work. No
            # stat-cache or old summary substitutes for current byte evidence.
            prefix = _inspect(directory, root, spec, control, templates, schedule=schedule, children=children)
            name = f"{index:08d}.json"
            if index < len(prefix):
                entry = prefix[index]
                if not entry["outcome_published"]:
                    _immutable_json(root / "outcomes" / name, entry["outcome"])
                if not entry["published"]:
                    _immutable_json(root / "results" / name, entry["result"])
                continue
            load_cpu_joint_campaign_bundle(directory)
            plan = _plan(spec, control, templates, prefix[:_SEARCHES], children=children) if row["stage"] == "reference" else None
            child = children[row["run_id"]] if row["stage"] == "search" else None
            execution = _execution(spec, row, child, plan)
            if plan is not None:
                _immutable_json(root / "plans" / f"{_PROFILE}.json",
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
            output = root / _PROFILE / ("searches" if child is not None else "references") / row["run_id"]
            evidence = {"child": _inventory(output)}
            if child is not None:
                evidence["bundle"] = _inventory(root / _PROFILE / "bundles" / row["run_id"])
            outcome = _outcome(row, child_prefix, evidence, error_type=error_type,
                               authorized=claim["dispatch_authorized"], output=output)
            _immutable_json(root / "outcomes" / name, outcome)
            load_cpu_joint_campaign_bundle(directory)
            _immutable_json(root / "results" / name, _result(claim, outcome))
        prefix = _inspect(directory, root, spec, control, templates, schedule=schedule, children=children)
        report = _report(spec, control, templates, schedule, prefix)
        _immutable_json(root / "report.json", report)
        return report


def run_cpu_joint_campaign(directory: Path, *, output_dir: Path) -> dict[str, Any]:
    """Execute only unclaimed scheduled work, retaining all failed/pending rows."""
    return _run(directory, output_dir=output_dir, execute=True)


def report_cpu_joint_campaign(directory: Path, *, output_dir: Path) -> dict[str, Any]:
    """Verify retained evidence without dispatch, materialization or writes."""
    return _run(directory, output_dir=output_dir, execute=False)
