"""Validate or execute the pinned tiny CPU inference study; GPU stays deferred."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from autocontext.weight_evolution.cpu_study import run_cpu_study, validate_cpu_study


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, default=Path(__file__).resolve().parent / "bundle")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("validate")
    run = commands.add_parser("run")
    run.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = validate_cpu_study(args.bundle) if args.command == "validate" else run_cpu_study(
        args.bundle, output_dir=args.output,
    )
    print(json.dumps(result, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
