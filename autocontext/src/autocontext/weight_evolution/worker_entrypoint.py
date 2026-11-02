"""Internal data-only adapter invoked by WeightDockerRunner inside its image."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from autocontext.util.json_io import write_json
from autocontext.weight_evolution.inference_models import load_inference_request
from autocontext.weight_evolution.models import load_reproduction_lock
from autocontext.weight_evolution.runtime import check_installed_runtime, load_runtime_lock


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("reconstruct", "infer"))
    parser.add_argument("--lock", required=True, type=Path)
    parser.add_argument("--runtime", required=True, type=Path)
    parser.add_argument("--asset-root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--variant", required=True, choices=("base", "published_patch"))
    parser.add_argument("--checkpoint", type=Path)
    parser.add_argument("--request", type=Path)
    parser.add_argument("--max-report-bytes", type=int, required=True)
    args = parser.parse_args(argv)
    try:
        if not 0 < args.max_report_bytes <= 256 * 1024 * 1024:
            raise ValueError("invalid report byte budget")
        if args.output.is_symlink() or not args.output.is_dir() or any(args.output.iterdir()):
            raise ValueError("worker output must be a fresh empty directory")
        lock = load_reproduction_lock(args.lock)
        runtime = load_runtime_lock(args.runtime)
        observed = check_installed_runtime(runtime)
        report: dict[str, object] = {
            "ok": True, "operation": args.operation, "lock_digest": lock.digest,
            "runtime_digest": runtime.digest, "observed_runtime": observed,
        }
        if args.operation == "reconstruct":
            if args.checkpoint is not None or args.request is not None:
                raise ValueError("reconstruction cannot accept inference inputs")
            from autocontext.weight_evolution.patch_worker import reconstruct_checkpoint

            checkpoint = reconstruct_checkpoint(lock, args.asset_root, args.output / "checkpoint", variant=args.variant)
            # This output directory is exclusive to one worker; the publisher's
            # coordination lock is not part of the exported checkpoint evidence.
            (args.output / ".checkpoint.lock").unlink(missing_ok=True)
            report["checkpoint"] = checkpoint.to_dict()
        else:
            if args.checkpoint is None or args.request is None:
                raise ValueError("inference requires a checkpoint and request")
            from autocontext.weight_evolution.inference_worker import run_inference

            request = load_inference_request(args.request)
            if request.variant != args.variant:
                raise ValueError("worker variant differs from request")
            result = run_inference(lock, args.asset_root, args.checkpoint, request, runtime)
            report["request_digest"] = request.digest
            report["inference"] = result.to_dict()
        encoded = json.dumps(report, sort_keys=True, indent=2, allow_nan=False).encode()
        if len(encoded) > args.max_report_bytes:
            raise ValueError("worker evidence exceeds report byte budget")
        write_json(args.output / "report.json", report)
        return 0
    except Exception as exc:
        print(json.dumps({"ok": False, "operation": args.operation,
                          "error": f"{type(exc).__name__}: {exc}"}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
