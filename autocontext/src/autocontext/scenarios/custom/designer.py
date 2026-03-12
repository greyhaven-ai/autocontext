from __future__ import annotations

import json
import re

from autocontext.scenarios.custom.spec import ScenarioSpec

SPEC_START = "<!-- SCENARIO_SPEC_START -->"
SPEC_END = "<!-- SCENARIO_SPEC_END -->"

_GRID_CTF_EXAMPLE = {
    "name": "grid_ctf",
    "display_name": "Grid CTF",
    "description": (
        "20x20 capture-the-flag map with fog of war and three unit archetypes "
        "(Scout, Soldier, Commander). Preserve at least one defender near base."
    ),
    "strategy_interface_description": (
        "Return JSON object with keys `aggression`, `defense`, and `path_bias`, "
        "all floats in [0,1]. Constraint: aggression + defense <= 1.4."
    ),
    "evaluation_criteria": (
        "Primary objective is capture progress. Secondary objectives are "
        "defender survivability and resource efficiency."
    ),
    "strategy_params": [
        {"name": "aggression", "description": "How aggressively units push forward",
         "min_value": 0.0, "max_value": 1.0, "default": 0.5},
        {"name": "defense", "description": "Defensive allocation near base",
         "min_value": 0.0, "max_value": 1.0, "default": 0.5},
        {"name": "path_bias", "description": "Preference for flanking vs direct routes",
         "min_value": 0.0, "max_value": 1.0, "default": 0.5},
    ],
    "constraints": [
        {"expression": "aggression + defense", "operator": "<=",
         "threshold": 1.4, "description": "aggression + defense must be <= 1.4"},
    ],
    "environment_variables": [
        {"name": "enemy_spawn_bias", "description": "Bias toward enemy placement",
         "low": 0.25, "high": 0.75},
        {"name": "resource_density", "description": "Density of map resources",
         "low": 0.1, "high": 0.9},
    ],
    "scoring_components": [
        {"name": "capture_progress", "description": "Flag capture progress",
         "formula_terms": {"aggression": 0.55, "path_bias": 0.45},
         "noise_range": [-0.07, 0.07]},
        {"name": "defender_survival", "description": "Defensive unit survival",
         "formula_terms": {"aggression": -0.4, "defense": 0.4},
         "noise_range": [-0.03, 0.03]},
        {"name": "energy_efficiency", "description": "Resource usage efficiency",
         "formula_terms": {"aggression": -0.3, "defense": 0.1},
         "noise_range": [-0.02, 0.02]},
    ],
    "final_score_weights": {
        "capture_progress": 0.6,
        "defender_survival": 0.25,
        "energy_efficiency": 0.15,
    },
    "win_threshold": 0.55,
    "observation_constraints": [
        "Maintain at least one defender near base.",
        "Avoid aggression spikes above sustainable energy budget.",
    ],
}

_SCHEMA_EXAMPLE = """\
{
  "name": "snake_case_identifier",
  "display_name": "Human Readable Name",
  "description": "Full rules description for agents",
  "strategy_interface_description": "JSON strategy schema",
  "evaluation_criteria": "Optimization objectives",
  "strategy_params": [
    {"name": "param_name", "description": "...",
     "min_value": 0.0, "max_value": 1.0, "default": 0.5}
  ],
  "constraints": [
    {"expression": "param_a + param_b", "operator": "<=",
     "threshold": 1.3, "description": "Budget constraint"}
  ],
  "environment_variables": [
    {"name": "env_var", "description": "...", "low": 0.1, "high": 0.9}
  ],
  "scoring_components": [
    {"name": "component", "description": "...",
     "formula_terms": {"param_a": 0.6, "param_b": 0.4},
     "noise_range": [-0.05, 0.05]}
  ],
  "final_score_weights": {"component": 1.0},
  "win_threshold": 0.55,
  "observation_constraints": ["Hint for the agent"]
}"""

SCENARIO_DESIGNER_SYSTEM = (
    "You are a scenario designer for AutoContext, a strategy evaluation system. "
    "Given a natural language description of a game scenario, produce a "
    "structured ScenarioSpec JSON that defines strategy parameters, "
    "scoring components, constraints, and environment variables.\n\n"
    f"The output must be valid JSON wrapped in delimiters:\n"
    f"{SPEC_START}\n{{ ... }}\n{SPEC_END}\n\n"
    f"## ScenarioSpec Schema\n\n```json\n{_SCHEMA_EXAMPLE}\n```\n\n"
    "## Rules\n\n"
    "- `name` must be valid snake_case\n"
    "- 3-6 strategy params, all floats with min/max ranges\n"
    "- At least 1 constraint linking params\n"
    "- At least 1 environment variable for stochastic state\n"
    "- 2-4 scoring components as weighted linear combos of params\n"
    "- `noise_range` values must be small: abs(noise) < 0.1\n"
    "- `final_score_weights` values must sum to exactly 1.0\n"
    "- `win_threshold` typically 0.5-0.6\n\n"
    f"## Example: Grid CTF\n\n{SPEC_START}\n"
    f"{json.dumps(_GRID_CTF_EXAMPLE, indent=2)}\n"
    f"{SPEC_END}\n\n"
    "Now design a scenario for the user's description.\n"
)


def parse_spec_from_response(text: str) -> ScenarioSpec:
    pattern = re.escape(SPEC_START) + r"\s*(.*?)\s*" + re.escape(SPEC_END)
    match = re.search(pattern, text, re.DOTALL)
    if not match:
        raise ValueError(
            "response does not contain SCENARIO_SPEC delimiters"
        )
    raw = match.group(1).strip()
    data = json.loads(raw)
    return ScenarioSpec.from_dict(data)
