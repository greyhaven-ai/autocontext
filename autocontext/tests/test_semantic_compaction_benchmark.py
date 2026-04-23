from __future__ import annotations

import pytest

from autocontext.scenarios.base import Observation


def test_benchmark_report_measures_semantic_signal_preservation() -> None:
    from autocontext.knowledge.semantic_compaction_benchmark import (
        build_semantic_compaction_benchmark_report,
    )
    from autocontext.prompts.templates import build_prompt_bundle

    observation = Observation(narrative="Investigate stalled optimizer", state={}, constraints=[])
    prompt_kwargs = {
        "scenario_rules": "rules",
        "strategy_interface": "interface",
        "evaluation_criteria": "criteria",
        "previous_summary": "best 0.5",
        "observation": observation,
        "current_playbook": (
            "## Lessons\n"
            + ("filler paragraph\n" * 140)
            + "- Root cause: stale hints kept pushing the same failing opening.\n"
            + "- Recommendation: preserve the rollback guard and diversify early probes.\n"
        ),
        "available_tools": "tools",
        "experiment_log": (
            "## Experiment Log\n\n"
            "### Generation 1\n"
            + ("noise line\n" * 120)
            + "\n### Generation 9\n"
            + "- Root cause: stale hints amplified retries.\n"
            + "- Recommendation: promote fresh evidence before tuning.\n"
        ),
        "session_reports": (
            "# Session Report: run_old\n"
            + ("filler paragraph\n" * 120)
            + "## Findings\n"
            + "- Preserve the rollback guard after failed harness mutations.\n"
        ),
        "context_budget_tokens": 180,
    }
    raw_components = {
        "playbook": prompt_kwargs["current_playbook"],
        "experiment_log": prompt_kwargs["experiment_log"],
        "session_reports": prompt_kwargs["session_reports"],
    }

    semantic_prompts = build_prompt_bundle(**prompt_kwargs)
    budget_only_prompts = build_prompt_bundle(**prompt_kwargs, semantic_compaction=False)
    report = build_semantic_compaction_benchmark_report(
        scenario_name="test_scenario",
        run_id="run_001",
        generation=3,
        context_budget_tokens=180,
        raw_components=raw_components,
        semantic_prompts=semantic_prompts,
        budget_only_prompts=budget_only_prompts,
        semantic_build_latency_ms=4.5,
        budget_only_build_latency_ms=2.0,
        evidence_cache_hits=1,
        evidence_cache_lookups=2,
    )

    assert report.raw_context_tokens > report.semantic_variant.context_tokens
    assert report.semantic_variant.signal_lines_preserved >= report.budget_only_variant.signal_lines_preserved
    assert report.evidence_cache_hit_rate == pytest.approx(0.5)
    assert any(check.name == "signal_preservation_non_regression" and check.passed for check in report.regression_checks)

