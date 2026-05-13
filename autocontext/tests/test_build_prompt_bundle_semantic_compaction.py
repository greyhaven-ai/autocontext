from __future__ import annotations

from typing import Any

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


def test_build_prompt_bundle_preserves_shared_evidence_when_budgeted() -> None:
    from autocontext.prompts.templates import build_prompt_bundle

    shared_evidence = "## Prior-Run Evidence\nSHARED-EVIDENCE"
    bundle = build_prompt_bundle(
        scenario_rules="rules",
        strategy_interface="interface",
        evaluation_criteria="criteria",
        previous_summary="summary",
        observation=Observation(narrative="test", state={}, constraints=[]),
        current_playbook="playbook",
        available_tools="tools",
        evidence_manifest=shared_evidence,
        context_budget_tokens=100_000,
        semantic_compaction=False,
    )

    assert "SHARED-EVIDENCE" in bundle.analyst
    assert "SHARED-EVIDENCE" in bundle.architect
    assert "SHARED-EVIDENCE" not in bundle.competitor


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


def test_build_prompt_bundle_records_compaction_entries() -> None:
    from autocontext.knowledge.compaction import CompactionEntry
    from autocontext.prompts.templates import build_prompt_bundle

    entries: list[CompactionEntry] = []
    build_prompt_bundle(
        scenario_rules="rules",
        strategy_interface="interface",
        evaluation_criteria="criteria",
        previous_summary="summary",
        observation=Observation(narrative="test", state={}, constraints=[]),
        current_playbook="playbook",
        available_tools="tools",
        experiment_log=(
            "## Experiment Log\n\n"
            "### Generation 1\n"
            + ("noise line\n" * 120)
            + "\n### Generation 9\n"
            + "- Root cause: stale hints amplified retries.\n"
        ),
        compaction_entry_context={"run_id": "run-1", "generation": 2},
        compaction_entry_parent_id="parent1",
        compaction_entry_sink=entries.extend,
    )

    assert len(entries) == 1
    assert entries[0].parent_id == "parent1"
    assert entries[0].details["run_id"] == "run-1"
    assert entries[0].details["generation"] == 2


def test_compaction_entry_uses_final_hook_mutated_summary() -> None:
    from autocontext.extensions import HookBus, HookEvents, HookResult
    from autocontext.knowledge.compaction import CompactionEntry
    from autocontext.prompts.templates import build_prompt_bundle

    bus = HookBus()
    entries: list[CompactionEntry] = []

    def after_compaction(event: Any) -> HookResult:
        components = dict(event.payload["components"])
        components["experiment_log"] = "HOOK FINAL COMPACTED: keep redirected summary"
        return HookResult(payload={"components": components})

    bus.on(HookEvents.AFTER_COMPACTION, after_compaction)

    build_prompt_bundle(
        scenario_rules="rules",
        strategy_interface="interface",
        evaluation_criteria="criteria",
        previous_summary="summary",
        observation=Observation(narrative="test", state={}, constraints=[]),
        current_playbook="playbook",
        available_tools="tools",
        experiment_log=(
            "## Experiment Log\n\n"
            "### Generation 1\n"
            + ("noise line\n" * 120)
            + "\n### Generation 9\n"
            + "- Root cause: stale hints amplified retries.\n"
        ),
        hook_bus=bus,
        compaction_entry_sink=entries.extend,
    )

    assert len(entries) == 1
    assert "HOOK FINAL COMPACTED" in entries[0].summary
