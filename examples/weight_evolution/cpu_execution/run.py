"""Attempt the predeclared CPU comparisons once; GPU execution remains deferred."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from autocontext.weight_evolution.cpu_execution_campaign import run_cpu_execution_campaign


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run_cpu_execution_campaign(args.bundle, output_dir=args.output),
                     sort_keys=True, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
