"""Validate, run, resume, or read a pinned CPU search campaign; no GPU execution."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from autocontext.weight_evolution.cpu_campaign import report_cpu_campaign, run_cpu_campaign, validate_cpu_campaign


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, default=Path(__file__).resolve().parent / "bundle")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("validate")
    for name in ("run", "report"):
        command = commands.add_parser(name)
        command.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "validate":
        result = validate_cpu_campaign(args.bundle)
    elif args.command == "run":
        result = run_cpu_campaign(args.bundle, output_dir=args.output)
    else:
        result = report_cpu_campaign(args.bundle, output_dir=args.output)
    print(json.dumps(result, sort_keys=True, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
