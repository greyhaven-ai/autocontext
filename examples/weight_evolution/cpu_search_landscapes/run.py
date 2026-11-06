"""Run only unclaimed slots of the declared measured CPU search-landscape campaign; GPU execution stays deferred."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from autocontext.weight_evolution.cpu_landscape_campaign import run_cpu_landscape_campaign


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    value = run_cpu_landscape_campaign(args.bundle, output_dir=args.output)
    print(json.dumps(value, sort_keys=True, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
