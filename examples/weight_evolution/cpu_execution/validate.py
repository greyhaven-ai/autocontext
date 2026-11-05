"""Check paired CPU assets, runtime, source pins and capacity without execution."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from autocontext.weight_evolution.cpu_execution_campaign import validate_cpu_execution_campaign


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(validate_cpu_execution_campaign(args.bundle), sort_keys=True, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
