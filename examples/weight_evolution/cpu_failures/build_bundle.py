"""Declare a CPU failure-observation campaign without running model inference."""

from __future__ import annotations

import argparse
from pathlib import Path

from autocontext.weight_evolution.cpu_failure_campaign import build_cpu_failure_campaign


def build_bundle(output: Path, *, preset: str = "fixture", campaign_id: str = "cpu-failure-pilot-v1") -> None:
    if preset not in {"fixture", "pilot"}:
        raise ValueError("unknown CPU failure campaign preset")
    build_cpu_failure_campaign(output, preset=preset, campaign_id=campaign_id)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--preset", choices=("fixture", "pilot"), default="fixture")
    parser.add_argument("--campaign-id", default="cpu-failure-pilot-v1")
    arguments = parser.parse_args()
    build_bundle(arguments.output, preset=arguments.preset, campaign_id=arguments.campaign_id)
