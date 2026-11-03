"""Validate and replay synthetic comparison fixtures; never execute a model."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from autocontext.weight_evolution.comparison_evaluation import (
    evaluate_comparison_fixture,
    write_comparison_report,
)
from autocontext.weight_evolution.comparison_fixtures import SyntheticComparisonScorer
from autocontext.weight_evolution.comparison_io import load_comparison_fixture


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture-root", type=Path, default=Path(__file__).with_name("fixtures"))
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("validate", help="verify frozen fixture bytes, identities, coverage and scorer inputs")
    run = commands.add_parser("run-fixtures", help="score prerecorded responses and write a synthetic comparison report")
    run.add_argument("--output", type=Path, required=True, help="new directory for JSON/Markdown fixture reports")
    args = parser.parse_args(argv)
    try:
        plan, evidence = load_comparison_fixture(args.fixture_root)
        evaluated = evaluate_comparison_fixture(plan, evidence, SyntheticComparisonScorer())
        result = {"ok": True, "execution_kind": "fixture", "model_execution_performed": False,
                  "gpu_execution_deferred": True, "plan_digest": plan.digest, "report_digest": evaluated.digest}
        if args.command == "run-fixtures":
            result.update(write_comparison_report(evaluated, args.output))
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except (OSError, ValueError, RuntimeError) as exc:
        print(json.dumps({"ok": False, "stage": args.command, "error": str(exc)}, indent=2), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
