"""AC-697 mission planner (slice 2).

Mirrors ``ts/src/mission/planner.ts`` (AC-435). Adaptive mission
planning over a pluggable LLM provider:

- ``decompose(goal)`` returns prioritised subgoals.
- ``plan_next_step(opts)`` decides the next action given the goal,
  completed steps, remaining subgoals, and verifier feedback.

The TS implementation depends on the codebase-wide ``LLMProvider``
interface; the Python port defines a minimal ``LLMProvider``
``Protocol`` here so slice-2 callers can drive the planner with any
shape (real provider, stub, recorded fixture). Future slices wire
the production provider.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Protocol

from autocontext.mission.events import _utc_now_iso as _now  # noqa: F401  # parity import marker

__all__ = [
    "LLMCompletion",
    "LLMCompletionRequest",
    "LLMProvider",
    "MissionPlanner",
    "PlanNextStepOpts",
    "PlanResult",
    "StepPlan",
    "SubgoalPlan",
    "VerifierFeedback",
]


# ---------------------------------------------------------------------------
# Provider abstraction
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LLMCompletionRequest:
    system_prompt: str
    user_prompt: str


@dataclass(frozen=True)
class LLMCompletion:
    text: str


class LLMProvider(Protocol):
    """Minimal LLM provider shape used by the planner.

    Slice 2 keeps this local so the planner can be unit-tested
    against a deterministic stub. A later slice can wire it to the
    package-wide LLM provider once the rest of the mission CLI is
    in place."""

    def complete(self, request: LLMCompletionRequest) -> LLMCompletion: ...


# ---------------------------------------------------------------------------
# Plan + step data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SubgoalPlan:
    description: str
    priority: int


@dataclass(frozen=True)
class PlanResult:
    subgoals: tuple[SubgoalPlan, ...]
    reasoning: str | None = None


@dataclass(frozen=True)
class VerifierFeedback:
    passed: bool
    reason: str
    suggestions: tuple[str, ...] = ()


@dataclass(frozen=True)
class PlanNextStepOpts:
    goal: str
    completed_steps: tuple[str, ...]
    remaining_subgoals: tuple[str, ...]
    verifier_feedback: VerifierFeedback | None = None


@dataclass(frozen=True)
class StepPlan:
    description: str
    reasoning: str
    should_revise: bool
    target_subgoal: str | None = None
    revised_subgoals: tuple[SubgoalPlan, ...] = field(default_factory=tuple)


# ---------------------------------------------------------------------------
# Prompts (TS parity)
# ---------------------------------------------------------------------------


_DECOMPOSE_SYSTEM = """You are a mission planner. Given a plain-language goal, decompose it into concrete, prioritized subgoals.

Output a JSON object with this shape:
{
  "subgoals": [
    { "description": "Concrete step description", "priority": 1 },
    { "description": "Next step", "priority": 2 }
  ],
  "reasoning": "Why this decomposition"
}

Rules:
- Priority 1 is highest (do first)
- Each subgoal should be specific and actionable
- Order by dependency: if B depends on A, A gets lower priority number
- 2-7 subgoals is ideal; avoid over-decomposition
- Output ONLY the JSON object, no markdown fences"""


_PLAN_STEP_SYSTEM_HEADER = (
    "You are an adaptive mission executor. Given the mission goal, "
    "completed steps, remaining subgoals, and verifier feedback, "
    "plan the next action."
)

_PLAN_STEP_SYSTEM = (
    _PLAN_STEP_SYSTEM_HEADER
    + """

Output a JSON object with this shape:
{
  "nextStep": "What to do next",
  "reasoning": "Why this is the right next step",
  "shouldRevise": false,
  "targetSubgoal": "Exact string from Remaining Subgoals"
}

If verifier feedback suggests the current plan is wrong, set shouldRevise: true and include revised subgoals:
{
  "nextStep": "What to do next",
  "reasoning": "Why we need to change approach",
  "shouldRevise": true,
  "revisedSubgoals": [
    { "description": "New step", "priority": 1 }
  ]
}

Rules:
- Base your decision on verifier feedback and completed work
- If feedback has suggestions, incorporate them
- Don't repeat already-completed steps
- When the next step advances an existing remaining subgoal, set targetSubgoal to the exact subgoal text from Remaining Subgoals
- If you are revising the plan instead of completing a current subgoal, omit targetSubgoal
- Be specific about what to do, not generic
- Output ONLY the JSON object"""
)  # noqa: E501


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_json(text: str) -> dict[str, Any] | None:
    """Mirror TS `parseJSON`: try the trimmed text first, then walk
    inward to find the outermost `{ ... }` block. Any failure
    returns None so the planner can fall back to a deterministic
    plan."""
    trimmed = text.strip()
    try:
        loaded = json.loads(trimmed)
        return loaded if isinstance(loaded, dict) else None
    except json.JSONDecodeError:
        pass
    start = trimmed.find("{")
    end = trimmed.rfind("}")
    if start != -1 and end > start:
        try:
            loaded = json.loads(trimmed[start : end + 1])
            return loaded if isinstance(loaded, dict) else None
        except json.JSONDecodeError:
            return None
    return None


def _coerce_subgoals(payload: list[Any]) -> tuple[SubgoalPlan, ...]:
    out: list[SubgoalPlan] = []
    for index, entry in enumerate(payload):
        if not isinstance(entry, dict):
            continue
        desc = entry.get("description")
        if not isinstance(desc, str) or not desc.strip():
            continue
        priority_value = entry.get("priority")
        priority = (
            priority_value if isinstance(priority_value, (int, float)) and not isinstance(priority_value, bool) else index + 1
        )
        out.append(SubgoalPlan(description=desc.strip(), priority=int(priority)))
    return tuple(sorted(out, key=lambda s: s.priority))


# ---------------------------------------------------------------------------
# MissionPlanner
# ---------------------------------------------------------------------------


class MissionPlanner:
    def __init__(self, provider: LLMProvider) -> None:
        self._provider = provider

    def decompose(self, goal: str) -> PlanResult:
        try:
            response = self._provider.complete(
                LLMCompletionRequest(
                    system_prompt=_DECOMPOSE_SYSTEM,
                    user_prompt=f"Mission goal: {goal}",
                )
            )
        except Exception:
            return self._fallback_plan(goal)

        parsed = _parse_json(response.text)
        if parsed is None or not isinstance(parsed.get("subgoals"), list):
            return self._fallback_plan(goal)

        subgoals = _coerce_subgoals(parsed["subgoals"])
        if not subgoals:
            return self._fallback_plan(goal)

        reasoning_raw = parsed.get("reasoning")
        reasoning = reasoning_raw if isinstance(reasoning_raw, str) else None
        return PlanResult(subgoals=subgoals, reasoning=reasoning)

    def plan_next_step(self, opts: PlanNextStepOpts) -> StepPlan:
        user_prompt = self._build_step_prompt(opts)
        try:
            response = self._provider.complete(LLMCompletionRequest(system_prompt=_PLAN_STEP_SYSTEM, user_prompt=user_prompt))
        except Exception:
            return self._fallback_step(opts)

        parsed = _parse_json(response.text)
        if parsed is None or not isinstance(parsed.get("nextStep"), str):
            return self._fallback_step(opts)

        description = str(parsed["nextStep"]).strip()
        reasoning_raw = parsed.get("reasoning")
        reasoning = str(reasoning_raw) if isinstance(reasoning_raw, str) else "Continuing mission"
        should_revise = parsed.get("shouldRevise") is True

        target_subgoal: str | None = None
        target_raw = parsed.get("targetSubgoal")
        if isinstance(target_raw, str) and target_raw in opts.remaining_subgoals:
            target_subgoal = target_raw
        elif not should_revise and len(opts.remaining_subgoals) == 1:
            target_subgoal = opts.remaining_subgoals[0]

        revised_subgoals: tuple[SubgoalPlan, ...] = ()
        if should_revise and isinstance(parsed.get("revisedSubgoals"), list):
            revised_subgoals = _coerce_subgoals(parsed["revisedSubgoals"])

        return StepPlan(
            description=description,
            reasoning=reasoning,
            should_revise=should_revise,
            target_subgoal=target_subgoal,
            revised_subgoals=revised_subgoals,
        )

    # ----------------------- prompt + fallbacks --------------------------

    def _build_step_prompt(self, opts: PlanNextStepOpts) -> str:
        sections: list[str] = [f"## Mission Goal\n{opts.goal}"]
        if opts.completed_steps:
            steps = "\n".join(f"{i + 1}. {description}" for i, description in enumerate(opts.completed_steps))
            sections.append(f"## Completed Steps\n{steps}")
        if opts.remaining_subgoals:
            bullets = "\n".join(f"- {sg}" for sg in opts.remaining_subgoals)
            sections.append(f"## Remaining Subgoals\n{bullets}")
        feedback = opts.verifier_feedback
        if feedback is not None:
            sections.append(f"## Verifier Feedback\nPassed: {str(feedback.passed).lower()}\nReason: {feedback.reason}")
            if feedback.suggestions:
                bullets = "\n".join(f"- {s}" for s in feedback.suggestions)
                sections.append(f"Suggestions:\n{bullets}")
        return "\n\n".join(sections)

    @staticmethod
    def _fallback_plan(goal: str) -> PlanResult:
        return PlanResult(
            subgoals=(SubgoalPlan(description=f"Work toward: {goal}", priority=1),),
            reasoning="Fallback: could not decompose goal via LLM",
        )

    @staticmethod
    def _fallback_step(opts: PlanNextStepOpts) -> StepPlan:
        if opts.remaining_subgoals:
            next_subgoal = opts.remaining_subgoals[0]
            return StepPlan(
                description=f"Work on: {next_subgoal}",
                reasoning="Fallback: could not plan step via LLM",
                should_revise=False,
                target_subgoal=next_subgoal,
            )
        return StepPlan(
            description=f"Continue: {opts.goal}",
            reasoning="Fallback: could not plan step via LLM",
            should_revise=False,
            target_subgoal=None,
        )
