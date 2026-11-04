"""Build a pinned generated CPU campaign, without running model inference."""

from __future__ import annotations

import argparse
from pathlib import Path

from autocontext.weight_evolution.cpu_campaign_tasks import (
    FIXTURE_TASK_SEEDS,
    PILOT_REPEATS,
    PILOT_TASK_SEEDS,
    build_cpu_campaign,
)


def build_bundle(output: Path, *, preset: str = "pilot") -> None:
    if preset not in {"fixture", "pilot"}:
        raise ValueError("unknown campaign preset")
    build_cpu_campaign(
        output, campaign_id=f"generated-cpu-search-{preset}-v1",
        task_seeds=FIXTURE_TASK_SEEDS if preset == "fixture" else PILOT_TASK_SEEDS,
        repeats=PILOT_REPEATS[:1] if preset == "fixture" else PILOT_REPEATS,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--preset", choices=("fixture", "pilot"), default="pilot")
    arguments = parser.parse_args()
    build_bundle(arguments.output, preset=arguments.preset)
