"""Example commands for local GPU prerequisites, acceptance and full replay."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from autocontext.weight_evolution.acceptance import acceptance_resource_summary, run_gpu_acceptance
from autocontext.weight_evolution.cohort import ReplayLimits, load_replay_plan, validate_plan_datasets
from autocontext.weight_evolution.datasets import load_dataset_lock, verify_datasets
from autocontext.weight_evolution.gpu_readiness import probe_gpu_readiness
from autocontext.weight_evolution.models import load_reproduction_lock
from autocontext.weight_evolution.reference import load_published_results
from autocontext.weight_evolution.reproduction import reproduction_resource_summary, run_reproduction
from autocontext.weight_evolution.runtime import load_runtime_lock
from autocontext.weight_evolution.scorer_runtime import load_pinned_scorer, load_scorer_lock

GPU_COMMANDS = {"check-gpu", "accept-gpu", "run-reproduction"}


def configure_gpu_commands(commands: Any) -> None:
    check = commands.add_parser("check-gpu", help="read-only local Docker/GPU prerequisites; creates no containers")
    check.add_argument("--gpu-device", required=True)
    check.add_argument("--output-parent", type=Path, required=True, help="filesystem intended for replay artifacts")
    check.add_argument("--required-free-bytes", type=int, required=True)
    check.add_argument("--memory-mb", type=int, default=16384)
    check.add_argument("--cpu-count", type=float, default=4.0)
    check.add_argument("--timeout-seconds", type=float, default=10.0)
    for name in ("accept-gpu", "run-reproduction"):
        command = commands.add_parser(name, help="inspect bounded work; --execute runs on the current GPU host")
        command.add_argument("--asset-root", type=Path, required=True)
        command.add_argument("--dataset-root", type=Path, required=True)
        command.add_argument("--scorer-root", type=Path, required=True)
        command.add_argument("--output", type=Path, required=True)
        command.add_argument("--gpu-device", required=True)
        command.add_argument("--execute", action="store_true")
        if name == "accept-gpu":
            command.add_argument("--max-total-generated-tokens", type=int, required=True)
            command.add_argument("--max-total-wall-seconds", type=float, required=True)
            command.add_argument("--smoke-max-new-tokens", type=int, default=64)
            command.add_argument("--shard-timeout-seconds", type=float, default=1800.0)
            command.add_argument("--memory-mb", type=int, default=16384)
            command.add_argument("--cpu-count", type=float, default=4.0)
            command.add_argument("--max-report-bytes", type=int, default=64 * 1024 * 1024)
        else:
            command.add_argument("--plan", type=Path, required=True)
            command.add_argument("--acceptance", type=Path, required=True, help="completed live accept-gpu directory")


def handle_gpu_command(args: argparse.Namespace) -> dict[str, Any]:
    runtime = load_runtime_lock(args.runtime)
    if args.command == "check-gpu":
        readiness = probe_gpu_readiness(runtime=runtime, gpu_device=args.gpu_device, output_parent=args.output_parent,
                                        required_free_bytes=args.required_free_bytes, memory_mb=args.memory_mb,
                                        cpu_count=args.cpu_count, timeout_seconds=args.timeout_seconds)
        return {"ok": readiness["ready_for_acceptance"], "executed": False, "readiness": readiness}
    if args.command not in GPU_COMMANDS:
        raise ValueError("unknown GPU operation")
    dataset_lock, scorer_lock = load_dataset_lock(args.datasets_lock), load_scorer_lock(args.scorer_lock)
    published = load_published_results(args.published_results)
    datasets = verify_datasets(dataset_lock, args.dataset_root)
    if args.command == "run-reproduction":
        plan = load_replay_plan(args.plan)
        if (dataset_lock.digest, scorer_lock.digest, published.digest, runtime.digest,
                load_reproduction_lock(args.lock).digest) != (
            plan.dataset_lock_digest, plan.scorer_lock_digest, plan.published_results_digest, plan.runtime_digest,
            plan.lock_digest,
        ):
            raise ValueError("evaluation/runtime locks differ from the frozen reproduction plan")
        validate_plan_datasets(plan, datasets)
        summary = reproduction_resource_summary(plan)
        if not args.execute:
            return {"ok": True, "executed": False, "acceptance_verified": False, **summary}
        print(json.dumps({"stage": "full_reproduction_resource_plan", **summary}, sort_keys=True), file=sys.stderr)
        scorer = load_pinned_scorer(scorer_lock, args.scorer_root)
        return run_reproduction(plan, acceptance_dir=args.acceptance, datasets=datasets, scorer=scorer,
                                published=published, lock_path=args.lock, runtime_path=args.runtime,
                                asset_root=args.asset_root, output_dir=args.output, gpu_device=args.gpu_device)
    limits = ReplayLimits(max_shard_problems=1, max_shard_generated_tokens=131072,
                          max_report_bytes=args.max_report_bytes, shard_timeout_seconds=args.shard_timeout_seconds,
                          max_total_generated_tokens=args.max_total_generated_tokens,
                          max_total_wall_seconds=args.max_total_wall_seconds, memory_mb=args.memory_mb,
                          cpu_count=args.cpu_count)
    options = dict(lock_path=args.lock, runtime_path=args.runtime, asset_root=args.asset_root, datasets=datasets,
                   scorer_lock_digest=scorer_lock.digest, published_results_digest=published.digest,
                   limits=limits, smoke_max_new_tokens=args.smoke_max_new_tokens)
    summary = acceptance_resource_summary(**options)
    if not args.execute:
        return {"ok": True, "executed": False, **summary}
    print(json.dumps({"stage": "gpu_acceptance_resource_plan", **summary}, sort_keys=True), file=sys.stderr)
    readiness = probe_gpu_readiness(runtime=runtime, gpu_device=args.gpu_device, output_parent=args.output.parent,
                                    required_free_bytes=summary["max_total_report_bytes"]
                                    + summary["checkpoint_storage_reservation_bytes"] + 512 * 1024 * 1024,
                                    memory_mb=args.memory_mb, cpu_count=args.cpu_count)
    if not readiness["ready_for_acceptance"]:
        return {"ok": False, "stage": "gpu_readiness", "executed": False, "readiness": readiness}
    scorer = load_pinned_scorer(scorer_lock, args.scorer_root)
    for dataset in datasets:
        scorer.validate_cohort(dataset.dataset, dataset.problems)
    # Resolve index selectors to the physical UUID observed before dispatch.
    # The worker's actual CUDA UUID is then independently compared in acceptance.
    uuid = readiness["observations"]["selected_gpu"]["uuid"]
    host = {"gpu_uuid": uuid, "driver_version": readiness["observations"]["selected_gpu"]["driver_version"],
            "daemon_id": readiness["observations"]["daemon"]["id"]}
    receipt = run_gpu_acceptance(**options, output_dir=args.output, gpu_device=uuid, expected_gpu_uuid=uuid,
                                 host_identity=host)
    return {"ok": True, "executed": True, "acceptance": receipt, "readiness": readiness}
