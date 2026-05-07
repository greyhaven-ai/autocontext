"""Tests for AC-734: ``solve --task-prompt`` verbatim mode.

The bug: ``autoctx solve -d "<full description>"`` runs the LLM scenario
designer, which (a) truncates briefs to 1000 chars and (b) generalizes
similar-shaped descriptions into a shared task_prompt, silently dropping
the discriminating content from the user's input.

The fix: a verbatim mode where the user's exact text becomes the
generated scenario's ``task_prompt`` — no LLM redesign, no truncation.

Domain shape:

- :class:`VerbatimSolveRequest` — value object: description, task_prompt,
  optional judge_rubric, optional name override.
- :func:`build_verbatim_solve_scenario` — builds an ``AgentTaskSpec``
  directly from the request and routes through the existing codegen +
  registry pipeline (DRY: same compile/register path as LLM-designed
  scenarios).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from autocontext.knowledge.verbatim_solve import (
    VerbatimSolveRequest,
    build_verbatim_solve_scenario,
)

# -- Value object --


class TestVerbatimSolveRequest:
    def test_minimum_required_fields(self):
        req = VerbatimSolveRequest(
            description="Prove that convexHull_subset_stdTri holds in Lean 4.",
            task_prompt="Produce a complete Lean 4 proof of the lemma...",
        )
        assert req.description.startswith("Prove that")
        assert "lemma" in req.task_prompt

    def test_judge_rubric_defaults_to_compile_clean_rubric(self):
        req = VerbatimSolveRequest(
            description="x",
            task_prompt="y",
        )
        # When no rubric is supplied, a sensible default is used so the
        # request alone can drive the build.
        assert req.judge_rubric.strip() != ""
        # Default should mention quality and threshold-style scoring.
        assert "0" in req.judge_rubric  # mentions a score range

    def test_explicit_judge_rubric_is_kept_verbatim(self):
        req = VerbatimSolveRequest(
            description="x",
            task_prompt="y",
            judge_rubric="Score 1.0 if MATCH-MARKER appears.",
        )
        assert req.judge_rubric == "Score 1.0 if MATCH-MARKER appears."

    def test_name_override_is_optional(self):
        req = VerbatimSolveRequest(description="x", task_prompt="y")
        assert req.name_override is None

    def test_explicit_name_override_is_carried(self):
        req = VerbatimSolveRequest(
            description="x",
            task_prompt="y",
            name_override="my_scenario_42",
        )
        assert req.name_override == "my_scenario_42"

    def test_empty_task_prompt_is_rejected(self):
        # The whole point of verbatim mode is preserving the user's prompt.
        # Empty defeats the purpose.
        with pytest.raises(ValueError):
            VerbatimSolveRequest(description="x", task_prompt="")

    def test_whitespace_only_task_prompt_is_rejected(self):
        with pytest.raises(ValueError):
            VerbatimSolveRequest(description="x", task_prompt="   \n  ")


# -- Build pipeline --


class TestBuildVerbatimSolveScenario:
    def test_returns_a_built_scenario_with_verbatim_prompt(self, tmp_path: Path):
        req = VerbatimSolveRequest(
            description="Prove convexHull_subset_stdTri",
            task_prompt="VERBATIM-PROMPT-MARKER: prove the lemma exactly.",
        )
        result = build_verbatim_solve_scenario(req, knowledge_root=tmp_path)
        # Build returns a scenario_name and confirms verbatim mode.
        assert result.scenario_name
        assert result.family_name == "agent_task"
        assert result.llm_classifier_fallback_used is False

    def test_no_llm_call_is_made(self, tmp_path: Path, monkeypatch):
        """Verbatim mode must NOT call the LLM designer (this is the whole point).

        We assert by introspection: if the designer were called, it would
        try to import its system prompt and run an LLM. Patch the designer
        and fail loudly if it fires.
        """
        from autocontext.scenarios.custom import agent_task_designer

        called = {"count": 0}

        def _explode(*args, **kwargs):
            called["count"] += 1
            raise AssertionError("LLM designer must NOT be called in verbatim mode")

        monkeypatch.setattr(agent_task_designer, "design_validated_agent_task", _explode)

        req = VerbatimSolveRequest(
            description="x",
            task_prompt="task prompt verbatim",
        )
        build_verbatim_solve_scenario(req, knowledge_root=tmp_path)
        assert called["count"] == 0

    def test_generated_scenario_class_returns_verbatim_task_prompt(
        self,
        tmp_path: Path,
    ):
        """The registered scenario's ``get_task_prompt`` must return the
        operator's exact text, not a designer-generalized version.
        """
        marker = "UNIQUE-MARKER-XYZ-9876"
        req = VerbatimSolveRequest(
            description="task: do the unique thing",
            task_prompt=f"Please do the following: {marker}",
        )
        result = build_verbatim_solve_scenario(req, knowledge_root=tmp_path)

        from autocontext.scenarios import SCENARIO_REGISTRY

        cls = SCENARIO_REGISTRY[result.scenario_name]
        instance = cls()
        prompt = instance.get_task_prompt(instance.initial_state())
        assert marker in prompt

    def test_name_override_wins_over_derived_name(self, tmp_path: Path):
        req = VerbatimSolveRequest(
            description="some description that would derive a name",
            task_prompt="x",
            name_override="explicit_name_foo",
        )
        result = build_verbatim_solve_scenario(req, knowledge_root=tmp_path)
        assert result.scenario_name == "explicit_name_foo"

    def test_default_name_is_derived_from_description(self, tmp_path: Path):
        # Same naming behavior as LLM-designed scenarios — derived from
        # the description so existing log/SQLite tooling continues to work.
        req = VerbatimSolveRequest(
            description="prove convexHull subset standard triangle",
            task_prompt="x",
        )
        result = build_verbatim_solve_scenario(req, knowledge_root=tmp_path)
        # Derived names are deterministic slugs.
        assert result.scenario_name
        assert "_" in result.scenario_name or result.scenario_name.isalnum()
