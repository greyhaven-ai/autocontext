"""Plan, validate, run, or report separate CPU practice diagnostics; GPUs stay deferred."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from autocontext.weight_evolution.cpu_diagnostic import (
    report_cpu_diagnostic,
    run_cpu_diagnostic,
    validate_cpu_diagnostic,
)
from autocontext.weight_evolution.cpu_diagnostic_plan import build_cpu_diagnostic_plan


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign-bundle", type=Path, required=True, help="original pinned M11 campaign bundle")
    parser.add_argument("--campaign-output", type=Path, required=True, help="complete retained M11 campaign evidence")
    parser.add_argument("--plan", type=Path, required=True, help="fresh plan directory for plan; pinned plan otherwise")
    commands = parser.add_subparsers(dest="command", required=True)
    plan = commands.add_parser("plan", help="verify every source run and freeze a separate diagnostic plan")
    plan.add_argument("--diagnostic-id", default="cpu-search-diagnostic-v1")
    commands.add_parser("validate", help="verify plan and complete source evidence without inference or writes")
    for name in ("run", "report"):
        command = commands.add_parser(name)
        command.add_argument("--output", type=Path, required=True, help="separate diagnostic evidence directory")
    args = parser.parse_args()
    if args.command == "plan":
        result = build_cpu_diagnostic_plan(
            args.campaign_bundle, args.campaign_output, output=args.plan, diagnostic_id=args.diagnostic_id,
        ).to_dict()
    elif args.command == "validate":
        result = validate_cpu_diagnostic(args.plan, campaign_bundle=args.campaign_bundle,
                                         campaign_output=args.campaign_output)
    elif args.command == "run":
        result = run_cpu_diagnostic(args.plan, campaign_bundle=args.campaign_bundle,
                                    campaign_output=args.campaign_output, output_dir=args.output)
    else:
        result = report_cpu_diagnostic(args.plan, campaign_bundle=args.campaign_bundle,
                                       campaign_output=args.campaign_output, output_dir=args.output)
    print(json.dumps(result, sort_keys=True, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
