"""Verify the measured CPU pilot assets, schedule and full capacity without inference."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from autocontext.weight_evolution.cpu_pilot_bundle import validate_cpu_pilot_bundle


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    args = parser.parse_args()
    value = validate_cpu_pilot_bundle(args.bundle)
    print(json.dumps(value, sort_keys=True, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
