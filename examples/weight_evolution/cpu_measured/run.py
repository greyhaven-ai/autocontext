"""Attempt each predeclared CPU profile once; GPU execution remains deferred."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from autocontext.weight_evolution.cpu_measured_acceptance import run_cpu_measured_acceptance


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    result = run_cpu_measured_acceptance(arguments.bundle, output_dir=arguments.output)
    print(json.dumps(result, sort_keys=True, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
