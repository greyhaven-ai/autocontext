#!/usr/bin/env python3
"""Emit the AC-798 OPD/GKD + GRPO matched-compute experiment plan."""

from __future__ import annotations

import argparse
import json
from importlib import import_module
from pathlib import Path
from typing import Any


def _protocol() -> Any:
    return import_module("autocontext.training.autoresearch.mixture_protocol")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", default="gsm8k")
    parser.add_argument("--seed", dest="seeds", action="append", type=int, default=[])
    parser.add_argument("--step", dest="steps", action="append", type=int, default=[])
    parser.add_argument("--prompts", type=int, default=384)
    parser.add_argument("--json", action="store_true", help="Write JSON instead of Markdown")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    protocol = _protocol()
    matrix = protocol.build_experiment_matrix(
        args.scenario,
        seeds=args.seeds or [0, 1, 2],
        steps=args.steps or [1000, 2000],
        prompts=args.prompts,
    )
    content = json.dumps(matrix, indent=2, sort_keys=True) + "\n" if args.json else protocol.render_protocol_report(matrix) + "\n"
    if args.output:
        args.output.write_text(content, encoding="utf-8")
    else:
        print(content, end="")


if __name__ == "__main__":
    main()
