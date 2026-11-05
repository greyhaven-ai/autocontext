"""Declare the complete measured CPU pilot without model execution."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from autocontext.weight_evolution.cpu_pilot_bundle import build_cpu_pilot_bundle, validate_cpu_pilot_bundle


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--campaign-id", default="cpu-measured-pilot-v1")
    args = parser.parse_args()
    build_cpu_pilot_bundle(args.output, campaign_id=args.campaign_id)
    value = validate_cpu_pilot_bundle(args.output)
    print(json.dumps(value, sort_keys=True, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
