from __future__ import annotations

import importlib
from typing import Any


def _module(name: str) -> Any:
    return importlib.import_module(name)


def _prompt_bundle(hint_style: str = "default") -> Any:
    templates = _module("autocontext.prompts.templates")
    base = _module("autocontext.scenarios.base")
    return templates.build_prompt_bundle(
        scenario_rules="Keep the flag safe.",
        strategy_interface='{"aggression": "number"}',
        evaluation_criteria="Score more than baseline.",
        previous_summary="best score so far: 0.1",
        observation=base.Observation(narrative="state", state={}, constraints=[]),
        current_playbook="Current playbook",
        available_tools="none",
        hint_style=hint_style,
    )


def test_structural_hint_prompt_is_opt_in() -> None:
    default_prompt = _prompt_bundle().coach
    structural_prompt = _prompt_bundle("structural").coach

    assert "avoid full target solutions" not in default_prompt
    assert "prefer constraints, invariants, verification checks" in structural_prompt
    assert "avoid full target solutions" in structural_prompt


def test_effective_hint_style_honors_env_toggle_semantics() -> None:
    soft_hints = _module("autocontext.knowledge.soft_hints")
    assert soft_hints.effective_hint_style(soft_hints_enabled=False, hint_style="default") == "default"
    assert soft_hints.effective_hint_style(soft_hints_enabled=True, hint_style="default") == "structural"
    assert soft_hints.effective_hint_style(soft_hints_enabled=False, hint_style="structural") == "structural"


def test_artifact_store_writes_hint_metadata(tmp_path: Any) -> None:
    soft_hints = _module("autocontext.knowledge.soft_hints")
    artifacts_mod = _module("autocontext.storage.artifacts")
    store = artifacts_mod.ArtifactStore(
        runs_root=tmp_path / "runs",
        knowledge_root=tmp_path / "knowledge",
        skills_root=tmp_path / "skills",
        claude_skills_path=tmp_path / ".claude" / "skills",
    )

    store.write_hints(
        "grid_ctf",
        "- Check the invariant",
        metadata=soft_hints.build_hint_metadata("Check the invariant", hint_style="structural"),
    )

    assert (tmp_path / "knowledge" / "grid_ctf" / "hints.meta.json").read_text(encoding="utf-8")


def test_hint_manager_persists_style_metadata() -> None:
    soft_hints = _module("autocontext.knowledge.soft_hints")
    hint_volume = _module("autocontext.knowledge.hint_volume")
    manager = hint_volume.HintManager(hint_volume.HintVolumePolicy(max_hints=3))
    manager.merge_hint_text(
        "- Check the defense invariant before changing aggression.",
        generation=2,
        metadata=soft_hints.build_hint_metadata(
            "Check the defense invariant before changing aggression.",
            hint_style="structural",
            support_evidence="score improved after invariant check",
        ),
    )

    metadata = manager.to_dict()["active"][0]["metadata"]
    assert metadata["hint_style"] == "structural"
    assert metadata["is_structural"] is True
    assert metadata["route_prescriptive"] is False
    assert metadata["support_evidence"] == "score improved after invariant check"


def test_hint_ab_report_summarizes_required_metrics() -> None:
    soft_hints = _module("autocontext.knowledge.soft_hints")
    report = soft_hints.build_hint_ab_report(
        [
            {
                "hint_style": "default",
                "score": 0.2,
                "response_length": 100,
                "novelty": 0.1,
                "rolled_back": True,
                "hint_adopted": False,
            },
            {
                "hint_style": "structural",
                "score": 0.4,
                "response_length": 80,
                "novelty": 0.3,
                "rolled_back": False,
                "hint_adopted": True,
            },
        ]
    )

    structural = report["styles"]["structural"]
    assert structural["mean_score"] == 0.4
    assert structural["mean_response_length"] == 80
    assert structural["mean_novelty"] == 0.3
    assert structural["rollback_rate"] == 0.0
    assert structural["hint_adoption_rate"] == 1.0
