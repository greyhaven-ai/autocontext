"""Validate or run bounded CPU tensor edits; model and GPU execution stay deferred."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from autocontext.weight_evolution.checkpoint_workflow import run_checkpoint_workflow, validate_checkpoint_workflow


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixtures", type=Path, default=Path(__file__).resolve().parent / "fixtures")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("validate")
    run = commands.add_parser("run-fixtures")
    run.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = validate_checkpoint_workflow(args.fixtures) if args.command == "validate" else run_checkpoint_workflow(
        args.fixtures, output_dir=args.output,
    )
    print(json.dumps(result, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
