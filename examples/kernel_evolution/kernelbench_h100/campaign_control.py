#!/usr/bin/env python3
"""Inspect or stop a durable kernel campaign without GPU/provider access."""

from __future__ import annotations

import argparse
from pathlib import Path

from profile_contract import PROFILE_NAMES, profile_output_root

from autocontext.kernel_evolution import (
    read_kernel_campaign_status,
    request_kernel_campaign_stop,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("status", "stop"))
    parser.add_argument("run_id")
    parser.add_argument("--output", type=Path, default=Path("runs/kernel-evolution-h100"))
    parser.add_argument("--precision-profile", choices=PROFILE_NAMES, required=True)
    parser.add_argument("--requested-by", default="operator")
    args = parser.parse_args()

    lineage_root = profile_output_root(args.output, args.precision_profile).resolve()
    if args.action == "stop":
        request_kernel_campaign_stop(lineage_root, args.run_id, requested_by=args.requested_by)
    status = read_kernel_campaign_status(lineage_root, args.run_id)
    print(status.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
