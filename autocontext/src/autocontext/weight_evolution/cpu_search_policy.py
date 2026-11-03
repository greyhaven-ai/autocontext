"""Pure bounded proposal decisions using only measured practice-score history."""

from __future__ import annotations

import json
from typing import Literal, Self

from pydantic import Field, model_validator

from autocontext.weight_evolution.artifacts import canonical_digest
from autocontext.weight_evolution.cpu_search_models import CpuSearchOption
from autocontext.weight_evolution.models import Digest, FrozenModel, Identifier


class CpuPracticeScore(FrozenModel):
    option_id: Identifier | None = Field(default=None, max_length=64)
    correct: int = Field(ge=0, le=16)
    total: int = Field(ge=2, le=16)

    @model_validator(mode="after")
    def possible_score(self) -> Self:
        if self.correct > self.total:
            raise ValueError("practice correct count exceeds cohort size")
        return self


class CpuSearchDecision(FrozenModel):
    option_id: Identifier | None
    reason: Literal["initial", "improvement", "switch", "fallback", "exhausted"]
    history_digest: Digest


def _history(history: tuple[CpuPracticeScore, ...]) -> tuple[CpuPracticeScore, ...]:
    if not isinstance(history, tuple) or not 1 <= len(history) <= 17:
        raise ValueError("practice history requires a bounded tuple including baseline")
    history = tuple(CpuPracticeScore.model_validate_json(json.dumps(row.to_dict())) for row in history)
    if history[0].option_id is not None or any(row.option_id is None for row in history[1:]):
        raise ValueError("practice history requires exactly one leading baseline")
    if len({row.option_id for row in history}) != len(history):
        raise ValueError("practice history repeats an option")
    if any(row.total != history[0].total for row in history):
        raise ValueError("every practice probe must cover the same complete cohort")
    return history


def select_best(history: tuple[CpuPracticeScore, ...]) -> CpuPracticeScore:
    """Keep the baseline or earliest trial on a tie over complete practice data."""
    return max(_history(history), key=lambda row: row.correct)


def choose_next_option(options: tuple[CpuSearchOption, ...],
                       history: tuple[CpuPracticeScore, ...]) -> CpuSearchDecision:
    """Continue an improving direction, otherwise try an unused direction.

    All ranking is derived from the passed practice counts. Option declaration
    order is the pinned tie-breaker, and no confirmation/audit data is accepted.
    Once all directions have been visited, try remaining options in declaration
    order. No option is evaluated twice.
    """
    if not isinstance(options, tuple) or not 1 <= len(options) <= 16:
        raise ValueError("proposal policy requires a bounded tuple of options")
    options = tuple(CpuSearchOption.model_validate_json(json.dumps(option.to_dict())) for option in options)
    if len({option.option_id for option in options}) != len(options) or len({option.mechanism for option in options}) != 1:
        raise ValueError("proposal policy requires distinct options from one mechanism")
    if len({(option.direction, option.step) for option in options}) != len(options):
        raise ValueError("proposal policy repeats a direction/step")
    history = _history(history)
    by_id = {option.option_id: option for option in options}
    if any(row.option_id not in by_id for row in history[1:]):
        raise ValueError("practice history refers to an unknown option")
    visited = {row.option_id for row in history[1:]}
    remaining = [option for option in options if option.option_id not in visited]
    history_digest = canonical_digest([row.to_dict() for row in history])

    def decision(option: CpuSearchOption | None, reason: Literal["initial", "improvement", "switch", "fallback", "exhausted"]
                 ) -> CpuSearchDecision:
        return CpuSearchDecision(option_id=option.option_id if option else None, reason=reason, history_digest=history_digest)

    if not remaining:
        return decision(None, "exhausted")
    if len(history) == 1:
        return decision(remaining[0], "initial")
    last_id = history[-1].option_id
    assert last_id is not None
    last = by_id[last_id]
    if history[-1].correct > max(row.correct for row in history[:-1]):
        continuation = next((option for option in remaining
                             if option.direction == last.direction and option.step == last.step + 1), None)
        if continuation is not None:
            return decision(continuation, "improvement")
    visited_directions = {by_id[option_id].direction for option_id in visited if option_id is not None}
    alternative = next((option for option in remaining if option.direction not in visited_directions), None)
    if alternative is not None:
        return decision(alternative, "switch")
    return decision(remaining[0], "fallback")
