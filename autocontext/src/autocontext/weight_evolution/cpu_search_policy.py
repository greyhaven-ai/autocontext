"""Pure bounded proposal decisions using only measured practice-score history."""

from __future__ import annotations

import hashlib
from typing import Literal, Self

from pydantic import Field, model_validator

from autocontext.weight_evolution.artifacts import canonical_digest
from autocontext.weight_evolution.cpu_search_models import CpuSearchOption, CpuSearchPolicy
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
    reason: Literal["initial", "improvement", "switch", "fallback", "fixed-order", "random-order", "exhausted"]
    history_digest: Digest
    policy: CpuSearchPolicy
    policy_digest: Digest

    @model_validator(mode="after")
    def bound_policy(self) -> Self:
        if self.policy_digest != self.policy.digest:
            raise ValueError("proposal decision differs from its policy identity")
        reasons = {"adaptive-v1": {"initial", "improvement", "switch", "fallback", "exhausted"},
                   "fixed-order-v1": {"fixed-order", "exhausted"}, "seeded-random-v1": {"random-order", "exhausted"}}
        if self.reason not in reasons[self.policy.name] or (self.option_id is None) != (self.reason == "exhausted"):
            raise ValueError("proposal reason differs from its policy or exhaustion state")
        return self


def _history(history: tuple[CpuPracticeScore, ...]) -> tuple[CpuPracticeScore, ...]:
    if not isinstance(history, tuple) or not 1 <= len(history) <= 17:
        raise ValueError("practice history requires a bounded tuple including baseline")
    history = tuple(CpuPracticeScore.model_validate(row.model_dump(warnings=False)) for row in history)
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


def _inputs(options: tuple[CpuSearchOption, ...], history: tuple[CpuPracticeScore, ...]
            ) -> tuple[tuple[CpuSearchOption, ...], tuple[CpuPracticeScore, ...]]:
    if not isinstance(options, tuple) or not 1 <= len(options) <= 16:
        raise ValueError("proposal policy requires a bounded tuple of options")
    options = tuple(CpuSearchOption.model_validate(option.model_dump(warnings=False)) for option in options)
    if len({option.option_id for option in options}) != len(options) or len({option.mechanism for option in options}) != 1:
        raise ValueError("proposal policy requires distinct options from one mechanism")
    if len({(option.direction, option.step) for option in options}) != len(options):
        raise ValueError("proposal policy repeats a direction/step")
    history = _history(history)
    by_id = {option.option_id: option for option in options}
    if any(row.option_id not in by_id for row in history[1:]):
        raise ValueError("practice history refers to an unknown option")
    return options, history


def choose_next_option(options: tuple[CpuSearchOption, ...],
                       history: tuple[CpuPracticeScore, ...]) -> CpuSearchDecision:
    """Continue an improving direction, otherwise try an unused direction.

    All ranking is derived from the passed practice counts. Option declaration
    order is the pinned tie-breaker, and no confirmation/audit data is accepted.
    Once all directions have been visited, try remaining options in declaration
    order. No option is evaluated twice. This remains the adaptive-v1 entrypoint.
    """
    options, history = _inputs(options, history)
    by_id = {option.option_id: option for option in options}
    visited = {row.option_id for row in history[1:]}
    remaining = [option for option in options if option.option_id not in visited]
    history_digest = canonical_digest([row.to_dict() for row in history])
    policy = CpuSearchPolicy()

    def decision(option: CpuSearchOption | None, reason: Literal["initial", "improvement", "switch", "fallback", "exhausted"]
                 ) -> CpuSearchDecision:
        return CpuSearchDecision(option_id=option.option_id if option else None, reason=reason, history_digest=history_digest,
                                 policy=policy, policy_digest=policy.digest)

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


def _random_rank(option_id: str, order_seed: int) -> bytes:
    identifier = option_id.encode()
    payload = (b"autocontext.cpu-search.seeded-random-v1\0" + order_seed.to_bytes(4, "big")
               + len(identifier).to_bytes(2, "big") + identifier)
    return hashlib.sha256(payload).digest()


def choose_policy_option(policy: CpuSearchPolicy, options: tuple[CpuSearchOption, ...],
                         history: tuple[CpuPracticeScore, ...]) -> CpuSearchDecision:
    """Dispatch a pinned proposal policy over the same validated practice data.

    The two controls ignore score values for proposal order, but require the
    same complete practice history. Selection still uses ``select_best``.
    Random order is a domain-separated SHA-256 ranking with option-ID ties;
    it is independent of Python hash randomization and input declaration order.
    """
    policy = CpuSearchPolicy.model_validate(policy.model_dump(warnings=False))
    if policy.name == "adaptive-v1":
        return choose_next_option(options, history)
    options, history = _inputs(options, history)
    ordered = options if policy.name == "fixed-order-v1" else tuple(sorted(
        options, key=lambda option: (_random_rank(option.option_id, policy.order_seed), option.option_id),
    ))
    if tuple(row.option_id for row in history[1:]) != tuple(option.option_id for option in ordered[:len(history) - 1]):
        raise ValueError("practice history is not a prefix of the pinned control order")
    visited = {row.option_id for row in history[1:]}
    remaining = [option for option in ordered if option.option_id not in visited]
    reason: Literal["fixed-order", "random-order", "exhausted"] = "exhausted"
    if remaining:
        reason = "fixed-order" if policy.name == "fixed-order-v1" else "random-order"
    return CpuSearchDecision(option_id=remaining[0].option_id if remaining else None, reason=reason,
                             history_digest=canonical_digest([row.to_dict() for row in history]),
                             policy=policy, policy_digest=policy.digest)
