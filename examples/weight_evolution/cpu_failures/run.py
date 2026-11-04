"""Run pinned CPU failure observations and practice diagnostics; GPUs remain deferred."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from autocontext.weight_evolution.cpu_failure_campaign import (
    report_cpu_failure_campaign,
    run_cpu_failure_campaign,
    validate_cpu_failure_campaign,
)
from autocontext.weight_evolution.cpu_failure_diagnostic import (
    build_cpu_failure_diagnostic_plan,
    report_cpu_failure_diagnostic,
    run_cpu_failure_diagnostic,
    validate_cpu_failure_diagnostic,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True, help="pinned CPU failure campaign bundle")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("validate", help="verify the campaign without execution or output writes")
    for name in ("run", "report"):
        command = commands.add_parser(name)
        command.add_argument("--output", type=Path, required=True)
    for name in ("diagnostic-plan", "diagnostic-validate", "diagnostic-run", "diagnostic-report"):
        command = commands.add_parser(name)
        command.add_argument("--campaign-output", type=Path, required=True, help="complete retained supervisor campaign")
        command.add_argument("--plan", type=Path, required=True, help="fresh plan for diagnostic-plan; pinned plan otherwise")
        if name == "diagnostic-plan":
            command.add_argument("--diagnostic-id", default="cpu-failure-reference-v1")
        elif name in {"diagnostic-run", "diagnostic-report"}:
            command.add_argument("--output", type=Path, required=True, help="separate diagnostic evidence directory")
    args = parser.parse_args()
    if args.command == "validate":
        result = validate_cpu_failure_campaign(args.bundle)
    elif args.command == "run":
        result = run_cpu_failure_campaign(args.bundle, output_dir=args.output)
    elif args.command == "report":
        result = report_cpu_failure_campaign(args.bundle, output_dir=args.output)
    elif args.command == "diagnostic-plan":
        result = build_cpu_failure_diagnostic_plan(
            args.bundle, args.campaign_output, output=args.plan, diagnostic_id=args.diagnostic_id,
        ).to_dict()
    elif args.command == "diagnostic-validate":
        result = validate_cpu_failure_diagnostic(args.plan, campaign_bundle=args.bundle,
                                                 campaign_output=args.campaign_output)
    elif args.command == "diagnostic-run":
        result = run_cpu_failure_diagnostic(args.plan, campaign_bundle=args.bundle,
                                            campaign_output=args.campaign_output, output_dir=args.output)
    else:
        result = report_cpu_failure_diagnostic(args.plan, campaign_bundle=args.bundle,
                                               campaign_output=args.campaign_output, output_dir=args.output)
    print(json.dumps(result, sort_keys=True, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
