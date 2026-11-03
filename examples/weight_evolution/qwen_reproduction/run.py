"""Verify Qwen replay assets, reconstruct checkpoints and run isolated inference."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from autocontext.weight_evolution.assets import DEFAULT_MAX_DOWNLOAD_BYTES, fetch_assets, select_assets, verify_assets
from autocontext.weight_evolution.gpu_commands import GPU_COMMANDS, configure_gpu_commands, handle_gpu_command
from autocontext.weight_evolution.models import load_reproduction_lock
from autocontext.weight_evolution.replay_commands import COMMANDS, configure_replay_commands, handle_replay_command


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lock", type=Path, default=Path(__file__).with_name("reproduction.lock.json"))
    parser.add_argument("--runtime", type=Path, default=Path(__file__).with_name("runtime.lock.json"))
    commands = parser.add_subparsers(dest="command", required=True)
    configure_replay_commands(parser, commands, Path(__file__).parent)
    configure_gpu_commands(commands)
    commands.add_parser("validate-lock", help="validate pinned metadata, without network access")
    for name in ("verify-assets", "fetch-assets"):
        command = commands.add_parser(name, help="verify local assets" if name == "verify-assets" else "fetch verified bytes")
        command.add_argument("--asset-root", type=Path, required=True)
        command.add_argument("--asset", action="append", help="explicit asset ID; defaults to required small assets")
        if name == "fetch-assets":
            command.add_argument("--max-download-bytes", type=int, default=DEFAULT_MAX_DOWNLOAD_BYTES)
            command.add_argument("--timeout-seconds", type=float, default=30.0)
    verify = commands.add_parser("verify-checkpoint", help="independently verify all checkpoint tensors offline")
    verify.add_argument("--asset-root", type=Path, required=True)
    verify.add_argument("--checkpoint", type=Path, required=True)
    for name in ("reconstruct", "infer"):
        command = commands.add_parser(name, help="print a resource plan; --execute runs the pinned Docker worker")
        command.add_argument("--asset-root", type=Path, required=True)
        command.add_argument("--output", type=Path, required=True, help="new directory for artifacts and execution receipt")
        command.add_argument("--variant", choices=("base", "published_patch"), default="published_patch")
        command.add_argument("--execute", action="store_true")
        command.add_argument("--timeout-seconds", type=float, default=1800.0)
        command.add_argument("--memory-mb", type=int, default=16384)
        command.add_argument("--cpu-count", type=float, default=4.0)
        if name == "infer":
            command.add_argument("--checkpoint", type=Path, required=True)
            command.add_argument("--request", type=Path, required=True)
            command.add_argument("--gpu-device", required=True, help="one explicit NVIDIA device, never 'all'")
    args = parser.parse_args(argv)
    try:
        if args.command in GPU_COMMANDS:
            gpu_result = handle_gpu_command(args)
            print(json.dumps(gpu_result, indent=2, sort_keys=True))
            return 0 if gpu_result.get("ok") is True else 1
        if args.command in COMMANDS:
            print(json.dumps(handle_replay_command(args), indent=2, sort_keys=True))
            return 0
        lock = load_reproduction_lock(args.lock)
        if args.command == "verify-checkpoint":
            from autocontext.weight_evolution.patch_worker import verify_checkpoint

            receipt = verify_checkpoint(lock, args.asset_root, args.checkpoint)
            print(json.dumps({"ok": True, "checkpoint": receipt.to_dict(), "receipt_digest": receipt.digest}, indent=2))
            return 0
        if args.command in {"reconstruct", "infer"}:
            from autocontext.weight_evolution.docker_runner import WeightDockerRunner, WeightWorkerLimits

            runner = WeightDockerRunner(limits=WeightWorkerLimits(
                timeout_seconds=args.timeout_seconds, memory_mb=args.memory_mb, cpu_count=args.cpu_count,
            ))
            options = dict(operation=args.command, lock_path=args.lock, runtime_path=args.runtime,
                           asset_root=args.asset_root, variant=args.variant,
                           checkpoint=getattr(args, "checkpoint", None), request_path=getattr(args, "request", None),
                           gpu_device=getattr(args, "gpu_device", None))
            plan = runner.plan(**options)
            if not args.execute:
                print(json.dumps({"ok": True, "executed": False, "plan": plan}, indent=2, sort_keys=True))
                return 0
            print(json.dumps({"stage": "resource_plan", "plan": plan}, sort_keys=True), file=sys.stderr)
            execution = runner.run(output_dir=args.output, **options)
            print(json.dumps(execution, indent=2, sort_keys=True))
            return 0
        if args.command == "validate-lock":
            required = select_assets(lock)
            result: dict[str, object] = {
                "stage": "contract_validation",
                "ok": True,
                "study_id": lock.study_id,
                "lock_digest": lock.digest,
                "source_revision": lock.source.revision,
                "model_revision": lock.model.revision,
                "required_assets": len(required),
                "required_download_bytes": sum(asset.size_bytes for asset in required),
                "optional_assets": [asset.asset_id for asset in lock.assets if not asset.required],
                "fidelity_gaps": list(lock.fidelity_gaps),
                "assets_verified": False,
                "model_execution_performed": False,
            }
            print(json.dumps(result, indent=2, sort_keys=True))
            return 0
        if args.command == "fetch-assets":
            report = fetch_assets(
                lock, args.asset_root, asset_ids=args.asset,
                max_download_bytes=args.max_download_bytes, timeout_seconds=args.timeout_seconds,
            )
        else:
            report = verify_assets(lock, args.asset_root, asset_ids=args.asset)
        print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
        return 0 if report.ok else 1
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print(json.dumps({"ok": False, "stage": args.command, "error": str(exc)}, indent=2), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
