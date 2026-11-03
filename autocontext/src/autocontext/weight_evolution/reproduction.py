"""Acceptance-gated full replay followed by independent official scoring.

This coordinator never provisions hardware. It requires a compatible local GPU
host, a frozen complete study and independently rechecked live acceptance.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from autocontext.util.file_lock import advisory_path_lock
from autocontext.weight_evolution.acceptance import verify_gpu_acceptance
from autocontext.weight_evolution.artifacts import MAX_CHECKPOINT_BYTES, canonical_digest, file_identity, load_small_json
from autocontext.weight_evolution.cohort import ReplayPlan, validate_plan_datasets
from autocontext.weight_evolution.datasets import VerifiedDataset
from autocontext.weight_evolution.evaluation import ReplayScorer, ReproductionEvaluation, evaluate_reproduction
from autocontext.weight_evolution.gpu_readiness import probe_gpu_readiness
from autocontext.weight_evolution.reference import PublishedResults
from autocontext.weight_evolution.replay import ReplayIncompleteError, _immutable_json, run_replay
from autocontext.weight_evolution.report import render_reproduction_report, write_reproduction_report
from autocontext.weight_evolution.runtime import load_runtime_lock


def reproduction_resource_summary(plan: ReplayPlan) -> dict[str, Any]:
    """Require the full three-dataset protocol, without starting workers."""
    if (plan.mode != "reproduction" or {c.dataset for c in plan.cohorts} != {"math500", "gsm8k", "ifeval"}
            or not all(c.complete_dataset for c in plan.cohorts)):
        raise ValueError("full reproduction requires all three complete locked datasets")
    return {
        **plan.resource_summary(), "scope": "accepted_gpu_full_reproduction",
        "acceptance_required": True, "official_scorer_preflight_required": True,
        "storage_reservation_bytes": (len(plan.shards) + 1) * plan.limits.max_report_bytes
        + MAX_CHECKPOINT_BYTES + 512 * 1024 * 1024,
        "storage_note": "Reports, one staging report/checkpoint and 512 MiB metadata/report reserve; "
        "excludes Docker/input caches.",
    }


def _publish_or_verify_report(evaluation: ReproductionEvaluation, directory: Path) -> dict[str, str]:
    if not directory.exists():
        return write_reproduction_report(evaluation, directory)
    if directory.is_symlink() or {p.name for p in directory.iterdir()} != {"complete.json", "report.json", "report.md"}:
        raise ReplayIncompleteError("existing reproduction report is incomplete; preserve it for reconciliation")
    expected = {"schema_version": 1, "report_digest": evaluation.digest, "plan_digest": evaluation.payload["plan_digest"]}
    markdown = render_reproduction_report(evaluation).encode()
    identity = file_identity(directory / "report.md", max_bytes=len(markdown))
    if (load_small_json(directory / "complete.json") != expected
            or load_small_json(directory / "report.json", max_bytes=256 * 1024 * 1024) != evaluation.to_dict()
            or identity["sha256"] != hashlib.sha256(markdown).hexdigest()):
        raise ReplayIncompleteError("existing reproduction report differs from independently regraded evidence")
    return {"report_digest": evaluation.digest, "json": str(directory / "report.json"),
            "markdown": str(directory / "report.md")}


def run_reproduction(
    plan: ReplayPlan, *, acceptance_dir: Path, datasets: tuple[VerifiedDataset, ...],
    scorer: ReplayScorer, published: PublishedResults, lock_path: Path, runtime_path: Path,
    asset_root: Path, output_dir: Path, gpu_device: str,
) -> dict[str, Any]:
    """Recheck acceptance and scorers, execute once, and publish complete scores.

Every resume repeats prerequisite/evidence verification. Existing replay claims
retain their original budget and ambiguous workers are never automatically retried.
"""
    summary = reproduction_resource_summary(plan)
    validate_plan_datasets(plan, datasets)
    if scorer.execution_kind != "official" or scorer.lock_digest != plan.scorer_lock_digest:
        raise ValueError("full reproduction requires the frozen official scorer")
    if published.digest != plan.published_results_digest:
        raise ValueError("published references differ from the full reproduction plan")
    # All CPU evaluator errors must be discovered before expensive dispatch.
    for dataset in datasets:
        scorer.validate_cohort(dataset.dataset, dataset.problems)
    root = output_dir.absolute()
    if any(p.is_symlink() for p in (root, *root.parents)):
        raise ValueError("reproduction output must not traverse symlinks")
    accepted = verify_gpu_acceptance(acceptance_dir, plan=plan, lock_path=lock_path, runtime_path=runtime_path,
                                     asset_root=asset_root, datasets=datasets)
    profile = {name: accepted[name] for name in ("harness_digest", "runtime_environment", "hardware")}
    # Existing raw reports consume part of the disk reservation. This allowance
    # never adopts their evidence; run_replay revalidates every byte before use.
    occupied = 0
    for shard in plan.shards:
        path = root / "replay" / "runs" / shard.shard_id / "report.json"
        if path.is_file() and not path.is_symlink():
            occupied += min(path.stat().st_size, plan.limits.max_report_bytes)
    readiness = probe_gpu_readiness(runtime=load_runtime_lock(runtime_path), gpu_device=gpu_device,
                                    output_parent=root.parent,
                                    required_free_bytes=summary["storage_reservation_bytes"] - occupied,
                                    memory_mb=plan.limits.memory_mb, cpu_count=plan.limits.cpu_count)
    if not readiness["ready_for_acceptance"]:
        return {"ok": False, "stage": "gpu_readiness", "executed": False, "readiness": readiness}
    uuid = readiness["observations"]["selected_gpu"]["uuid"]
    if uuid != accepted["hardware"]["device_uuid"]:
        raise ValueError("selected GPU UUID differs from live acceptance")
    host = {"gpu_uuid": uuid, "driver_version": readiness["observations"]["selected_gpu"]["driver_version"],
            "daemon_id": readiness["observations"]["daemon"]["id"]}
    if accepted.get("host_identity") != host:
        raise ValueError("GPU driver or Docker daemon differs from live acceptance; repeat acceptance on this host")
    manifest = {"schema_version": 1, "plan_digest": plan.digest,
                "acceptance_digest": canonical_digest(accepted), "accepted_execution": profile,
                "scorer_provenance": scorer.provenance(), "gpu_uuid": uuid}
    root.parent.mkdir(parents=True, exist_ok=True)
    with advisory_path_lock(root.parent / f".{root.name}.reproduction.lock"):
        if root.exists():
            if load_small_json(root / "manifest.json", max_bytes=1024 * 1024) != manifest:
                raise ReplayIncompleteError("reproduction inputs changed; cannot resume")
        else:
            root.mkdir()
            _immutable_json(root / "manifest.json", manifest)
            _immutable_json(root / "readiness.initial.json", readiness)
        replay = run_replay(
            plan, datasets=datasets, lock_path=lock_path, runtime_path=runtime_path, asset_root=asset_root,
            checkpoints={v: acceptance_dir / "reconstruction" / v / "checkpoint" for v in ("base", "published_patch")},
            output_dir=root / "replay", gpu_device=uuid, expected_execution=profile,
        )
        evaluation = evaluate_reproduction(
            plan, {d.dataset: d for d in datasets}, scorer,
            {p.name: p for p in (root / "replay" / "runs").iterdir()}, published=published,
        )
        if evaluation.payload["evidence_kind"] != "full_cohort_replay":
            raise ValueError("full reproduction cannot publish smoke, subset or fixture evidence")
        report = _publish_or_verify_report(evaluation, root / "report")
        receipt = {"schema_version": 1, "ok": True, "scope": "accepted_gpu_full_reproduction",
                   "manifest_digest": canonical_digest(manifest), "plan_digest": plan.digest,
                   "acceptance_digest": canonical_digest(accepted), "report_digest": evaluation.digest,
                   "completed_shards": replay["completed_shards"], "model_execution_performed": True,
                   "behavioral_reproduction_claimed": False}
        marker = root / "reproduction.receipt.json"
        if marker.exists():
            if load_small_json(marker, max_bytes=1024 * 1024) != receipt:
                raise ReplayIncompleteError("reproduction completion receipt changed")
        else:
            _immutable_json(marker, receipt)
        return {**receipt, "executed": True, "dispatched_this_invocation": replay["dispatched_this_invocation"],
                "report": report}
