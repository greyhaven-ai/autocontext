from __future__ import annotations

import json
from pathlib import Path

from autocontext.config.settings import AppSettings
from autocontext.loop.levy_scout import LevyScoutConfig, evaluate_levy_scout, render_levy_scout_guidance
from autocontext.prompts.templates import build_prompt_bundle
from autocontext.scenarios.base import Observation

FIXTURE_PATH = Path(__file__).resolve().parents[2] / "docs" / "levy-scout-parity-fixtures.json"


def test_levy_scout_matches_shared_fixtures() -> None:
    fixtures = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))

    for case in fixtures["cases"]:
        outcome = evaluate_levy_scout(
            LevyScoutConfig(enabled=True, alpha=fixtures["alpha"], scale=fixtures["scale"]),
            seed_base=case["seed_base"],
            generation=case["generation"],
            attempt=case["attempt"],
        )
        assert abs(outcome.random_value - case["random_value"]) < 1e-15
        assert abs(outcome.step_size - case["step_size"]) < 1e-15
        assert outcome.intensity == case["intensity"]


def test_levy_scout_guidance_is_default_off_and_prompt_visible() -> None:
    assert AppSettings().experimental_levy_scout_enabled is False
    assert render_levy_scout_guidance(LevyScoutConfig(enabled=False), seed_base=0, generation=1) == ""

    guidance = render_levy_scout_guidance(
        LevyScoutConfig(enabled=True, alpha=1.5, scale=0.2),
        seed_base=0,
        generation=10,
    )
    prompts = build_prompt_bundle(
        scenario_rules="rules",
        strategy_interface='{"aggression": float}',
        evaluation_criteria="score",
        previous_summary="best score so far: 0.5",
        observation=Observation(narrative="obs", state={}, constraints=[]),
        current_playbook="playbook",
        available_tools="",
        scout_mutation_guidance=guidance,
    )

    assert "Lévy scout mutation guidance" in prompts.competitor
    assert "jump" in prompts.competitor
    assert "Lévy scout mutation guidance" not in prompts.analyst
