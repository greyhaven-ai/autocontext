"""Explicit toy scoring for offline comparison fixtures, never benchmark grading.

Only integer arithmetic and three small instruction checkers are supported. The
real reproduction's extraction/aggregation boundaries are reused, but these
checkers are not substitutes for math-verify or the official IFEval registry.
"""

from __future__ import annotations

import copy
import hashlib
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Literal, Protocol

from autocontext.weight_evolution.artifacts import canonical_digest
from autocontext.weight_evolution.datasets import EvaluationProblem
from autocontext.weight_evolution.scorers import (
    IFEvalInstruction,
    InstructionRegistry,
    ScorerInfrastructureError,
    score_ifeval,
    score_math,
)

_INTEGER = re.compile(r"^[+-]?[0-9]{1,128}$")
_SCORER_SPEC: dict[str, Any] = {
    "name": "synthetic-comparison-fixture-v1",
    "integer_equivalence": "signed-decimal-up-to-128-digits",
    "instruction_checkers": {
        "fixture:contains_word": "casefolded-whitespace-token-membership",
        "fixture:word_count": "python-str-split-count",
        "fixture:uppercase": "python-str-isupper",
    },
    "thinking": "require-closing-think-for-instructions",
    "execution_kind": "fixture",
    "official_benchmark_grader": False,
    "benchmark_membership": False,
}


class ComparisonSample(Protocol):
    @property
    def problem_id(self) -> str: ...

    @property
    def text(self) -> str: ...

    @property
    def finish_reason(self) -> Literal["stop", "length"]: ...


def _integer(value: str) -> int | None:
    stripped = value.strip()
    return int(stripped) if _INTEGER.fullmatch(stripped) else None


def _integer_equivalent(prediction: str, gold: str) -> bool:
    expected = _integer(gold)
    if expected is None:
        raise ScorerInfrastructureError("fixture math gold must be a bounded signed decimal integer")
    return _integer(prediction) == expected


class _FixtureInstruction:
    def __init__(self, instruction_id: str) -> None:
        self.instruction_id = instruction_id
        self.arguments: dict[str, object] | None = None

    def build_description(self, **kwargs: object) -> str:
        if self.instruction_id == "fixture:contains_word":
            word = kwargs.get("word")
            valid = (set(kwargs) == {"word"} and isinstance(word, str) and 0 < len(word) <= 128
                     and len(word.split()) == 1 and word == word.strip())
        elif self.instruction_id == "fixture:word_count":
            count = kwargs.get("count")
            valid = (set(kwargs) == {"count"} and isinstance(count, int) and not isinstance(count, bool)
                     and 0 <= count <= 1000)
        else:
            valid = self.instruction_id == "fixture:uppercase" and not kwargs
        if not valid:
            raise ValueError("invalid synthetic instruction arguments")
        self.arguments = dict(kwargs)
        return self.instruction_id

    def get_instruction_args(self) -> Sequence[str] | None:
        if self.arguments is None:
            raise ValueError("synthetic instruction has not been initialized")
        return tuple(self.arguments)

    def check_following(self, value: str) -> bool:
        if self.arguments is None:
            raise ValueError("synthetic instruction has not been initialized")
        if self.instruction_id == "fixture:contains_word":
            return str(self.arguments["word"]).casefold() in value.casefold().split()
        if self.instruction_id == "fixture:word_count":
            return len(value.split()) == self.arguments["count"]
        return value.isupper()


_FIXTURE_REGISTRY: InstructionRegistry = {name: _FixtureInstruction for name in _SCORER_SPEC["instruction_checkers"]}


class SyntheticComparisonScorer:
    """A fixture-only scorer with a content-bound, explicit implementation identity."""

    execution_kind = "fixture"

    def provenance(self) -> dict[str, Any]:
        # Pin the toy behavior independently of user-provided fixture responses.
        source_sha256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        shared_sha256 = hashlib.sha256(Path(__file__).with_name("scorers.py").read_bytes()).hexdigest()
        return {**copy.deepcopy(_SCORER_SPEC), "implementation_sha256": source_sha256,
                "shared_scorers_sha256": shared_sha256}

    @property
    def lock_digest(self) -> str:
        return canonical_digest(self.provenance())

    def validate_cohort(self, dataset: str, problems: Sequence[EvaluationProblem]) -> None:
        if dataset not in {"math500", "gsm8k", "ifeval"}:
            raise ScorerInfrastructureError("unknown comparison fixture dataset route")
        if not problems or len({p.problem_id for p in problems}) != len(problems):
            raise ScorerInfrastructureError("fixture cohort must be nonempty with unique problem IDs")
        for problem in problems:
            if dataset in {"math500", "gsm8k"}:
                if problem.answer is None or _integer(problem.answer) is None or problem.instructions:
                    raise ScorerInfrastructureError("fixture math gold must be a bounded signed decimal integer")
            else:
                if problem.answer is not None or not problem.instructions:
                    raise ScorerInfrastructureError("instruction fixture requires explicit checker arguments")
                for instruction in problem.instructions:
                    try:
                        checker = _FIXTURE_REGISTRY[instruction.instruction_id](instruction.instruction_id)
                        checker.build_description(**instruction.kwargs)
                    except (KeyError, TypeError, ValueError) as exc:
                        raise ScorerInfrastructureError("unknown or malformed synthetic instruction") from exc

    def score_sample(self, dataset: str, problem: EvaluationProblem, sample: ComparisonSample) -> int:
        if sample.problem_id != problem.problem_id:
            raise ScorerInfrastructureError("fixture response belongs to a different problem")
        self.validate_cohort(dataset, (problem,))
        if dataset in {"math500", "gsm8k"}:
            assert problem.answer is not None
            return score_math(sample.text, problem.answer, verifier=_integer_equivalent,
                              finish_reason=sample.finish_reason).score
        return score_ifeval(
            sample.text, tuple(IFEvalInstruction(item.instruction_id, item.kwargs) for item in problem.instructions),
            prompt=problem.text, registry=_FIXTURE_REGISTRY, thinking=True, finish_reason=sample.finish_reason,
        ).score
