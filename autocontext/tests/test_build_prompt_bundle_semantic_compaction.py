from __future__ import annotations

from autocontext.scenarios.base import Observation


def test_build_prompt_bundle_accepts_role_specific_evidence_manifests() -> None:
    from autocontext.prompts.templates import build_prompt_bundle

    bundle = build_prompt_bundle(
        scenario_rules="rules",
        strategy_interface="interface",
        evaluation_criteria="criteria",
        previous_summary="summary",
        observation=Observation(narrative="test", state={}, constraints=[]),
        current_playbook="playbook",
        available_tools="tools",
        evidence_manifests={
            "analyst": "## Prior-Run Evidence (Analyst)\nA1",
            "architect": "## Prior-Run Evidence (Architect)\nB1",
        },
    )

    assert "Prior-Run Evidence (Analyst)" in bundle.analyst
    assert "Prior-Run Evidence (Architect)" in bundle.architect
    assert "Prior-Run Evidence (Architect)" not in bundle.analyst


def test_build_prompt_bundle_compacts_history_before_budget_fallback() -> None:
    from autocontext.prompts.templates import build_prompt_bundle

    bundle = build_prompt_bundle(
        scenario_rules="rules",
        strategy_interface="interface",
        evaluation_criteria="criteria",
        previous_summary="summary",
        observation=Observation(narrative="test", state={}, constraints=[]),
        current_playbook="playbook",
        available_tools="tools",
        experiment_log=(
            "## RLM Experiment Log\n\n"
            "### Generation 1\n"
            + ("noise line\n" * 120)
            + "\n### Generation 7\n"
            + "- Root cause: overfitting to stale hints\n"
        ),
        session_reports=(
            "# Session Report: run_old\n"
            + ("filler paragraph\n" * 80)
            + "## Findings\n"
            + "- Preserve the rollback guard after failed harness mutations.\n"
        ),
    )

    assert "Generation 7" in bundle.competitor
    assert "rollback guard" in bundle.competitor
    assert "condensed" in bundle.competitor.lower()
