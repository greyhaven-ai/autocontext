"""AC-913 baseline: how often does markdown-heading scraping actually find the sections?

Runs the REAL analyst task instruction from prompts/templates.py through
llama3.1:8b, then feeds the response to the REAL _extract_section_bullets from
agents/parsers.py. A section counts as lost when the model was asked for it and
the parser returns an empty list -- which today is silent, not an error.

Usage: uv run --frozen python ac913_baseline.py <n_trials>
"""
# ruff: noqa: E402 - sys.path is set below before the package imports

from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from autocontext.agents.parsers import _extract_section_bullets

# Verbatim from prompts/templates.py:450 (the analyst_task string).
ANALYST_TASK = "Analyze strengths/failures and return markdown with sections: Findings, Root Causes, Actionable Recommendations."

SECTIONS = ("Findings", "Root Causes", "Actionable Recommendations")

# A plausible analyst input: a scenario run that partly failed.
CONTEXT = """Scenario: grid_ctf, generation 3.
Competitor strategy: greedy nearest-flag with a 2-step lookahead, aggression=0.8.
Result: score 0.41 of 1.0. Captured 2 of 5 flags.
Failures: agent oscillated between two equidistant flags for 14 of 40 steps;
walked into a guarded tile twice; never used the decoy action.
Strengths: reached the first flag in 6 steps, below the 9-step median."""

MODEL = "llama3.1:8b"
URL = "http://localhost:11434/api/chat"


def ask(temperature: float, seed: int) -> str:
    payload = {
        "model": MODEL,
        "messages": [
            {
                "role": "system",
                "content": "You are the analyst agent in an optimization loop.",
            },
            {"role": "user", "content": f"{CONTEXT}\n\n{ANALYST_TASK}"},
        ],
        "stream": False,
        "options": {"temperature": temperature, "seed": seed},
    }
    req = urllib.request.Request(
        URL,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=300) as resp:
        return json.loads(resp.read())["message"]["content"]


def main() -> None:
    trials = int(sys.argv[1]) if len(sys.argv) > 1 else 20
    lost: dict[str, int] = dict.fromkeys(SECTIONS, 0)
    any_lost = 0
    all_lost = 0
    records = []

    for i in range(trials):
        # Temperature 0.7 is the realistic setting for a creative analyst turn;
        # a fresh seed per trial so this measures drift, not one lucky sample.
        text = ask(temperature=0.7, seed=1000 + i)
        found = {s: _extract_section_bullets(text, s) for s in SECTIONS}
        missing = [s for s, b in found.items() if not b]
        for s in missing:
            lost[s] += 1
        if missing:
            any_lost += 1
        if len(missing) == len(SECTIONS):
            all_lost += 1
        records.append({"seed": 1000 + i, "missing": missing, "chars": len(text), "text": text})
        print(f"  trial {i + 1:2d}/{trials}: missing={missing or 'none'}", flush=True)

    print()
    print(f"model={MODEL}  trials={trials}  temperature=0.7")
    for s in SECTIONS:
        print(f"  {s:28s} lost {lost[s]:2d}/{trials}  ({100 * lost[s] / trials:.0f}%)")
    print(
        f"  {'>=1 section lost':28s}      {any_lost:2d}/{trials}  ({100 * any_lost / trials:.0f}%)"
    )
    print(
        f"  {'all sections lost':28s}      {all_lost:2d}/{trials}  ({100 * all_lost / trials:.0f}%)"
    )

    out = Path(__file__).resolve().parent / "ac913_baseline_result.json"
    out.write_text(
        json.dumps(
            {
                "model": MODEL,
                "trials": trials,
                "temperature": 0.7,
                "lost_per_section": lost,
                "at_least_one_lost": any_lost,
                "all_lost": all_lost,
                "records": records,
            },
            indent=2,
        )
        + "\n"
    )
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
