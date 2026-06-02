"""AC-697 mission planner tests (slice 2).

Mirrors the unit-test surface for ``ts/src/mission/planner.ts``.
Uses a deterministic stub provider so the planner can be exercised
without a real LLM. Covers happy-path decomposition + step planning,
JSON-with-markdown-fence parsing, fallback when the provider throws
or returns garbage, target subgoal binding, and the single-remaining
subgoal auto-bind branch.
"""

from __future__ import annotations

from autocontext.mission import (
    LLMCompletion,
    LLMCompletionRequest,
    LLMProvider,
    MissionPlanner,
    PlanNextStepOpts,
    VerifierFeedback,
)


class _StubProvider:
    """Deterministic stub. Either returns a fixed response or raises
    a configured exception."""

    def __init__(
        self,
        response_text: str = "",
        exc: Exception | None = None,
    ) -> None:
        self._response_text = response_text
        self._exc = exc
        self.calls: list[LLMCompletionRequest] = []

    def complete(self, request: LLMCompletionRequest) -> LLMCompletion:
        self.calls.append(request)
        if self._exc is not None:
            raise self._exc
        return LLMCompletion(text=self._response_text)


def test_stub_provider_satisfies_protocol() -> None:
    """Protocol parity check."""
    provider: LLMProvider = _StubProvider("{}")
    assert provider.complete(LLMCompletionRequest(system_prompt="x", user_prompt="y")).text == "{}"


# ---------------------------------------------------------------------------
# decompose
# ---------------------------------------------------------------------------


def test_decompose_happy_path_sorts_by_priority() -> None:
    provider = _StubProvider(
        '{"subgoals": [{"description": "B", "priority": 2}, {"description": "A", "priority": 1}], "reasoning": "ok"}'
    )
    planner = MissionPlanner(provider)
    result = planner.decompose("ship login")
    assert [s.description for s in result.subgoals] == ["A", "B"]
    assert result.reasoning == "ok"


def test_decompose_strips_markdown_fence_around_json() -> None:
    provider = _StubProvider('```json\n{"subgoals": [{"description": "only", "priority": 1}]}\n```')
    planner = MissionPlanner(provider)
    result = planner.decompose("ship login")
    assert [s.description for s in result.subgoals] == ["only"]


def test_decompose_falls_back_when_provider_throws() -> None:
    provider = _StubProvider(exc=RuntimeError("boom"))
    planner = MissionPlanner(provider)
    result = planner.decompose("ship login")
    assert len(result.subgoals) == 1
    assert "Fallback" in (result.reasoning or "")
    assert result.subgoals[0].description == "Work toward: ship login"


def test_decompose_falls_back_when_subgoals_field_missing() -> None:
    provider = _StubProvider('{"reasoning": "x"}')
    planner = MissionPlanner(provider)
    result = planner.decompose("ship login")
    assert "Fallback" in (result.reasoning or "")


def test_decompose_drops_invalid_subgoal_entries() -> None:
    """Entries without a string description are filtered out; the
    rest survive (TS parity)."""
    provider = _StubProvider('{"subgoals": [{"description": "keep"}, {"description": "  "}, "bogus", {"priority": 1}]}')
    planner = MissionPlanner(provider)
    result = planner.decompose("ship login")
    assert [s.description for s in result.subgoals] == ["keep"]


# ---------------------------------------------------------------------------
# plan_next_step
# ---------------------------------------------------------------------------


def test_plan_next_step_happy_path_binds_target_subgoal() -> None:
    provider = _StubProvider(
        '{"nextStep": "configure oauth", "reasoning": "first", "shouldRevise": false, "targetSubgoal": "set up oauth"}'
    )
    planner = MissionPlanner(provider)
    plan = planner.plan_next_step(
        PlanNextStepOpts(
            goal="ship login",
            completed_steps=(),
            remaining_subgoals=("set up oauth", "wire callback"),
        )
    )
    assert plan.description == "configure oauth"
    assert plan.target_subgoal == "set up oauth"
    assert plan.should_revise is False


def test_plan_next_step_auto_binds_single_remaining_subgoal() -> None:
    """If the model omits targetSubgoal and only one subgoal remains,
    the planner pins that subgoal automatically (TS parity)."""
    provider = _StubProvider('{"nextStep": "ship", "reasoning": "last one", "shouldRevise": false}')
    planner = MissionPlanner(provider)
    plan = planner.plan_next_step(
        PlanNextStepOpts(
            goal="ship login",
            completed_steps=("wire oauth",),
            remaining_subgoals=("final QA",),
        )
    )
    assert plan.target_subgoal == "final QA"


def test_plan_next_step_should_revise_carries_revised_subgoals() -> None:
    provider = _StubProvider(
        '{"nextStep": "pivot", "reasoning": "wrong direction",'
        ' "shouldRevise": true,'
        ' "revisedSubgoals": [{"description": "new", "priority": 1}]}'
    )
    planner = MissionPlanner(provider)
    plan = planner.plan_next_step(
        PlanNextStepOpts(
            goal="ship login",
            completed_steps=(),
            remaining_subgoals=("old",),
        )
    )
    assert plan.should_revise is True
    assert [s.description for s in plan.revised_subgoals] == ["new"]
    # On revise we drop the auto-bind branch
    assert plan.target_subgoal is None


def test_plan_next_step_ignores_target_subgoal_not_in_remaining_list() -> None:
    provider = _StubProvider('{"nextStep": "do x", "reasoning": "x", "shouldRevise": false, "targetSubgoal": "unknown"}')
    planner = MissionPlanner(provider)
    plan = planner.plan_next_step(
        PlanNextStepOpts(
            goal="g",
            completed_steps=(),
            remaining_subgoals=("known1", "known2"),
        )
    )
    assert plan.target_subgoal is None


def test_plan_next_step_falls_back_when_response_missing_next_step() -> None:
    provider = _StubProvider('{"reasoning": "no next step"}')
    planner = MissionPlanner(provider)
    plan = planner.plan_next_step(
        PlanNextStepOpts(
            goal="g",
            completed_steps=(),
            remaining_subgoals=("only",),
        )
    )
    assert "Fallback" in plan.reasoning
    assert plan.description == "Work on: only"
    assert plan.target_subgoal == "only"


def test_plan_next_step_falls_back_when_provider_throws() -> None:
    provider = _StubProvider(exc=RuntimeError("boom"))
    planner = MissionPlanner(provider)
    plan = planner.plan_next_step(
        PlanNextStepOpts(
            goal="g",
            completed_steps=(),
            remaining_subgoals=(),
        )
    )
    assert "Fallback" in plan.reasoning
    assert plan.description == "Continue: g"
    assert plan.target_subgoal is None


def test_plan_next_step_prompt_carries_completed_steps_and_feedback() -> None:
    provider = _StubProvider('{"nextStep": "next", "reasoning": "x", "shouldRevise": false}')
    planner = MissionPlanner(provider)
    planner.plan_next_step(
        PlanNextStepOpts(
            goal="ship login",
            completed_steps=("did a",),
            remaining_subgoals=("do b",),
            verifier_feedback=VerifierFeedback(passed=False, reason="r", suggestions=("hint",)),
        )
    )
    prompt = provider.calls[0].user_prompt
    assert "## Mission Goal\nship login" in prompt
    assert "## Completed Steps\n1. did a" in prompt
    assert "## Remaining Subgoals\n- do b" in prompt
    assert "## Verifier Feedback" in prompt
    assert "Suggestions:\n- hint" in prompt
