"""AC-913 after-measurement: same prompt, same model, constrained decoding on.

Goes through the REAL OpenAICompatibleProvider (not raw curl), so this measures
the shipped code path including the response_format wiring and the `constrained`
flag, not a hand-rolled request.
"""
# ruff: noqa: E402 - sys.path is set below before the package imports

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from autocontext.providers.base import OutputSchema
from autocontext.providers.openai_compat import OpenAICompatibleProvider

ANALYST_TASK = "Analyze strengths/failures and return markdown with sections: Findings, Root Causes, Actionable Recommendations."

CONTEXT = """Scenario: grid_ctf, generation 3.
Competitor strategy: greedy nearest-flag with a 2-step lookahead, aggression=0.8.
Result: score 0.41 of 1.0. Captured 2 of 5 flags.
Failures: agent oscillated between two equidistant flags for 14 of 40 steps;
walked into a guarded tile twice; never used the decoy action.
Strengths: reached the first flag in 6 steps, below the 9-step median."""

ANALYST_SCHEMA = OutputSchema(
    name="analyst_output",
    schema={
        "type": "object",
        "properties": {
            "findings": {"type": "array", "items": {"type": "string"}},
            "root_causes": {"type": "array", "items": {"type": "string"}},
            "recommendations": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["findings", "root_causes", "recommendations"],
        "additionalProperties": False,
    },
)

REQUIRED = ("findings", "root_causes", "recommendations")


def main() -> None:
    trials = int(sys.argv[1]) if len(sys.argv) > 1 else 20
    provider = OpenAICompatibleProvider(
        api_key="ollama",
        base_url="http://localhost:11434/v1",
        default_model_name="llama3.1:8b",
    )

    lost = dict.fromkeys(REQUIRED, 0)
    any_lost = 0
    unconstrained = 0
    records = []

    for i in range(trials):
        result = provider.complete(
            system_prompt="You are the analyst agent in an optimization loop.",
            user_prompt=f"{CONTEXT}\n\n{ANALYST_TASK}",
            temperature=0.7,
            max_tokens=1200,
            output_schema=ANALYST_SCHEMA,
        )
        if not result.constrained:
            unconstrained += 1

        missing: list[str] = []
        try:
            payload = json.loads(result.text)
        except json.JSONDecodeError:
            missing = list(REQUIRED)
        else:
            missing = [k for k in REQUIRED if not payload.get(k)]

        for k in missing:
            lost[k] += 1
        if missing:
            any_lost += 1
        records.append(
            {"missing": missing, "constrained": result.constrained, "text": result.text}
        )
        print(
            f"  trial {i + 1:2d}/{trials}: missing={missing or 'none'} constrained={result.constrained}",
            flush=True,
        )

    print()
    print(
        f"model=llama3.1:8b  trials={trials}  temperature=0.7  constrained decoding ON"
    )
    for k in REQUIRED:
        print(f"  {k:28s} lost {lost[k]:2d}/{trials}  ({100 * lost[k] / trials:.0f}%)")
    print(
        f"  {'>=1 section lost':28s}      {any_lost:2d}/{trials}  ({100 * any_lost / trials:.0f}%)"
    )
    print(f"  {'reported unconstrained':28s}      {unconstrained:2d}/{trials}")

    out = Path(__file__).resolve().parent / "ac913_after_result.json"
    out.write_text(
        json.dumps(
            {
                "model": "llama3.1:8b",
                "trials": trials,
                "lost_per_field": lost,
                "at_least_one_lost": any_lost,
                "reported_unconstrained": unconstrained,
                "records": records,
            },
            indent=2,
        )
        + "\n"
    )
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
