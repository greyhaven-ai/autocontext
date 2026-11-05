"""Declare a fixed, two-profile CPU acceptance schedule without inference."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from autocontext.weight_evolution.cpu_measured_acceptance import (
    build_cpu_measured_acceptance,
    validate_cpu_measured_acceptance,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--acceptance-id", default="cpu-measured-parity-v1")
    parser.add_argument("--requests", type=int, choices=range(1, 5), default=4)
    arguments = parser.parse_args()
    build_cpu_measured_acceptance(arguments.output, acceptance_id=arguments.acceptance_id,
                                   request_count=arguments.requests)
    print(json.dumps(validate_cpu_measured_acceptance(arguments.output), sort_keys=True, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
