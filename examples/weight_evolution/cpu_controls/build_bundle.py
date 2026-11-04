"""Build a pinned CPU transfer-control campaign without running model inference."""

from __future__ import annotations

import argparse
from pathlib import Path

from autocontext.weight_evolution.cpu_control_tasks import build_cpu_control_campaign


def build_bundle(output: Path, *, preset: str = "pilot") -> None:
    if preset not in {"fixture", "pilot"}:
        raise ValueError("unknown control campaign preset")
    build_cpu_control_campaign(output, preset=preset)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--preset", choices=("fixture", "pilot"), default="pilot")
    arguments = parser.parse_args()
    build_bundle(arguments.output, preset=arguments.preset)
