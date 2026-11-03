"""Validate/fetch opaque reproduction assets; never run a model or source script."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from autocontext.weight_evolution.assets import DEFAULT_MAX_DOWNLOAD_BYTES, fetch_assets, select_assets, verify_assets
from autocontext.weight_evolution.models import load_reproduction_lock


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lock", type=Path, default=Path(__file__).with_name("reproduction.lock.json"))
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("validate-lock", help="validate pinned metadata, without network access")
    for name in ("verify-assets", "fetch-assets"):
        command = commands.add_parser(name, help="verify local assets" if name == "verify-assets" else "fetch verified bytes")
        command.add_argument("--asset-root", type=Path, required=True)
        command.add_argument("--asset", action="append", help="explicit asset ID; defaults to required small assets")
        if name == "fetch-assets":
            command.add_argument("--max-download-bytes", type=int, default=DEFAULT_MAX_DOWNLOAD_BYTES)
            command.add_argument("--timeout-seconds", type=float, default=30.0)
    args = parser.parse_args(argv)
    try:
        lock = load_reproduction_lock(args.lock)
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
    except (OSError, ValueError) as exc:
        print(json.dumps({"ok": False, "stage": args.command, "error": str(exc)}, indent=2), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
