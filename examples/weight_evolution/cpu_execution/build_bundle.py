"""Declare the fixed paired CPU search/reference schedule without inference."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from autocontext.weight_evolution.cpu_execution_campaign import (
    build_cpu_execution_campaign,
    validate_cpu_execution_campaign,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--campaign-id", default="cpu-execution-paired-v1")
    args = parser.parse_args()
    build_cpu_execution_campaign(args.output, campaign_id=args.campaign_id)
    print(json.dumps(validate_cpu_execution_campaign(args.output), sort_keys=True, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
