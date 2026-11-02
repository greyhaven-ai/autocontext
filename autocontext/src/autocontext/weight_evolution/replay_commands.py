"""Example-only command wiring for frozen Qwen cohort replay and reporting."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from autocontext.weight_evolution.cohort import ReplayLimits, build_replay_plan, load_replay_plan, validate_plan_datasets
from autocontext.weight_evolution.datasets import fetch_datasets, load_dataset_lock, verify_datasets
from autocontext.weight_evolution.models import load_reproduction_lock
from autocontext.weight_evolution.reference import load_published_results
from autocontext.weight_evolution.runtime import load_runtime_lock
from autocontext.weight_evolution.scorer_runtime import (
    fetch_scorer_assets,
    load_pinned_scorer,
    load_scorer_lock,
    verify_scorer_assets,
)

COMMANDS = {"fetch-datasets", "verify-datasets", "fetch-scorers", "verify-scorers", "preflight-scorers",
            "plan-replay", "run-replay", "report-replay"}


def configure_replay_commands(parser: argparse.ArgumentParser, commands: Any, example: Path) -> None:
    parser.add_argument("--datasets-lock", type=Path, default=example / "datasets.lock.json")
    parser.add_argument("--scorer-lock", type=Path, default=example / "scorer.lock.json")
    parser.add_argument("--published-results", type=Path, default=example / "published-results.lock.json")
    for name in ("fetch-datasets", "verify-datasets"):
        command = commands.add_parser(name, help="fetch or verify the pinned benchmark bytes")
        command.add_argument("--dataset-root", type=Path, required=True)
        command.add_argument("--dataset", choices=("math500", "gsm8k", "ifeval"), action="append")
        if name == "fetch-datasets":
            command.add_argument("--max-download-bytes", type=int, default=16 * 1024 * 1024)
    for name in ("fetch-scorers", "verify-scorers", "preflight-scorers"):
        command = commands.add_parser(name, help="fetch, verify or load the pinned official scoring environment")
        command.add_argument("--scorer-root", type=Path, required=True)
        if name == "preflight-scorers":
            command.add_argument("--dataset-root", type=Path, required=True)
    plan = commands.add_parser("plan-replay", help="freeze paired cohorts and finite resource ceilings; executes no model")
    plan.add_argument("--asset-root", type=Path, required=True)
    plan.add_argument("--dataset-root", type=Path, required=True)
    plan.add_argument("--base-checkpoint", type=Path, required=True)
    plan.add_argument("--patch-checkpoint", type=Path, required=True)
    plan.add_argument("--output", type=Path, required=True, help="new plan JSON file")
    plan.add_argument("--dataset", choices=("math500", "gsm8k", "ifeval"), action="append")
    plan.add_argument("--max-total-generated-tokens", type=int, required=True)
    plan.add_argument("--max-total-wall-seconds", type=float, required=True)
    plan.add_argument("--max-shard-problems", type=int, default=8)
    plan.add_argument("--max-shard-generated-tokens", type=int, default=1048576)
    plan.add_argument("--max-report-bytes", type=int, default=64 * 1024 * 1024)
    plan.add_argument("--shard-timeout-seconds", type=float, default=1800.0)
    plan.add_argument("--memory-mb", type=int, default=16384)
    plan.add_argument("--cpu-count", type=float, default=4.0)
    plan.add_argument("--smoke-max-new-tokens", type=int)
    plan.add_argument("--smoke-problems", type=int, default=16)
    run = commands.add_parser("run-replay", help="inspect frozen replay; --execute dispatches the planned GPU work")
    run.add_argument("--plan", type=Path, required=True)
    run.add_argument("--asset-root", type=Path, required=True)
    run.add_argument("--dataset-root", type=Path, required=True)
    run.add_argument("--base-checkpoint", type=Path, required=True)
    run.add_argument("--patch-checkpoint", type=Path, required=True)
    run.add_argument("--output", type=Path, required=True, help="new replay directory, or exact prior directory to reconcile")
    run.add_argument("--gpu-device", required=True)
    run.add_argument("--execute", action="store_true")
    report = commands.add_parser("report-replay", help="independently score saved evidence and write JSON/Markdown")
    report.add_argument("--plan", type=Path, required=True)
    report.add_argument("--dataset-root", type=Path, required=True)
    report.add_argument("--scorer-root", type=Path, required=True)
    report.add_argument("--runs-root", type=Path, required=True, help="replay directory containing runs/<shard_id>")
    report.add_argument("--output", type=Path, required=True)
    report.add_argument("--allow-fixture", action="store_true", help="label synthetic evidence as fixture, never reproduction")


def handle_replay_command(args: argparse.Namespace) -> dict[str, Any]:
    name = args.command
    if name in {"fetch-datasets", "verify-datasets"}:
        dataset_lock = load_dataset_lock(args.datasets_lock)
        if name == "fetch-datasets":
            datasets = fetch_datasets(dataset_lock, args.dataset_root, dataset_names=args.dataset,
                                      max_download_bytes=args.max_download_bytes)
        else:
            datasets = verify_datasets(dataset_lock, args.dataset_root, dataset_names=args.dataset)
        return {"ok": True, "dataset_lock_digest": dataset_lock.digest,
                "datasets": [dataset.to_dict() for dataset in datasets]}
    if name in {"fetch-scorers", "verify-scorers", "preflight-scorers"}:
        scorer_lock = load_scorer_lock(args.scorer_lock)
        if name == "fetch-scorers":
            return fetch_scorer_assets(scorer_lock, args.scorer_root)
        if name == "verify-scorers":
            return verify_scorer_assets(scorer_lock, args.scorer_root)
        scorer = load_pinned_scorer(scorer_lock, args.scorer_root)
        datasets = verify_datasets(load_dataset_lock(args.datasets_lock), args.dataset_root)
        for dataset in datasets:
            scorer.validate_cohort(dataset.dataset, dataset.problems)
        return {"ok": True, "scorer": scorer.provenance(), "validated_datasets": [d.to_dict() for d in datasets]}
    if name == "plan-replay":
        from autocontext.weight_evolution.patch_worker import verify_checkpoint

        lock, runtime = load_reproduction_lock(args.lock), load_runtime_lock(args.runtime)
        datasets = verify_datasets(load_dataset_lock(args.datasets_lock), args.dataset_root, dataset_names=args.dataset)
        receipts = (verify_checkpoint(lock, args.asset_root, args.base_checkpoint, expected_variant="base"),
                    verify_checkpoint(lock, args.asset_root, args.patch_checkpoint, expected_variant="published_patch"))
        limits = ReplayLimits(
            max_shard_problems=args.max_shard_problems, max_shard_generated_tokens=args.max_shard_generated_tokens,
            max_report_bytes=args.max_report_bytes, shard_timeout_seconds=args.shard_timeout_seconds,
            max_total_generated_tokens=args.max_total_generated_tokens, max_total_wall_seconds=args.max_total_wall_seconds,
            memory_mb=args.memory_mb, cpu_count=args.cpu_count,
        )
        plan = build_replay_plan(
            lock, runtime, datasets, receipts, scorer_lock_digest=load_scorer_lock(args.scorer_lock).digest,
            published_results_digest=load_published_results(args.published_results).digest, limits=limits,
            mode="smoke" if args.smoke_max_new_tokens is not None else "reproduction",
            smoke_max_new_tokens=args.smoke_max_new_tokens, smoke_problem_limit=args.smoke_problems,
        )
        if any(p.is_symlink() for p in (args.output.absolute(), *args.output.absolute().parents)):
            raise ValueError("plan output must not traverse symlinks")
        with args.output.open("x", encoding="utf-8") as stream:
            stream.write(json.dumps(plan.to_dict(), indent=2, sort_keys=True) + "\n")
        return {"ok": True, "plan_path": str(args.output), **plan.resource_summary()}
    if name not in {"run-replay", "report-replay"}:
        raise ValueError("unknown replay operation")
    plan = load_replay_plan(args.plan)
    dataset_lock = load_dataset_lock(args.datasets_lock)
    scorer_lock = load_scorer_lock(args.scorer_lock)
    published = load_published_results(args.published_results)
    if (dataset_lock.digest, scorer_lock.digest, published.digest) != (
        plan.dataset_lock_digest, plan.scorer_lock_digest, plan.published_results_digest,
    ):
        raise ValueError("dataset/scorer/reference locks differ from the frozen plan")
    datasets = verify_datasets(dataset_lock, args.dataset_root, dataset_names=[c.dataset for c in plan.cohorts])
    validate_plan_datasets(plan, datasets)
    if name == "run-replay":
        if not args.execute:
            return {"ok": True, "executed": False, **plan.resource_summary()}
        from autocontext.weight_evolution.replay import run_replay

        print(json.dumps({"stage": "replay_resource_plan", **plan.resource_summary()}, sort_keys=True), file=sys.stderr)
        return run_replay(
            plan, datasets=datasets, lock_path=args.lock, runtime_path=args.runtime, asset_root=args.asset_root,
            checkpoints={"base": args.base_checkpoint, "published_patch": args.patch_checkpoint},
            output_dir=args.output, gpu_device=args.gpu_device,
        )
    from autocontext.weight_evolution.evaluation import evaluate_reproduction
    from autocontext.weight_evolution.report import write_reproduction_report

    scorer = load_pinned_scorer(scorer_lock, args.scorer_root)
    evaluation = evaluate_reproduction(
        plan, {dataset.dataset: dataset for dataset in datasets}, scorer,
        {path.name: path for path in (args.runs_root / "runs").iterdir()},
        allow_fixture=args.allow_fixture, published=published,
    )
    return {"ok": True, **write_reproduction_report(evaluation, args.output)}
