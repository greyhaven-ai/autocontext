"""Validate or run pinned CPU editing-session fixtures; never launch inference."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from autocontext.weight_evolution.session_workflow import run_session_workflow, validate_session_workflow


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixtures", type=Path, default=Path(__file__).resolve().parent / "fixtures")
    parser.add_argument("--comparison-fixtures", type=Path,
                        default=Path(__file__).resolve().parents[1] / "qwen_comparison" / "fixtures")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("validate")
    run = commands.add_parser("run-fixtures")
    run.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "validate":
        result = validate_session_workflow(args.fixtures, args.comparison_fixtures)
    else:
        result = run_session_workflow(args.fixtures, comparison_directory=args.comparison_fixtures,
                                      output_dir=args.output)
    print(json.dumps(result, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
