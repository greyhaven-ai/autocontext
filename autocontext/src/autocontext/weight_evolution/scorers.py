"""Offline scoring boundaries for the pinned Qwen weight-edit reproduction.

Extraction/metric semantics follow ``exp8_math/code/{mathqa,evaluate}.py`` at
RikiyaT/lm_understand-logs@4d09ba6d6a7e891dd8063974dfe81b3d78e3494f. Math
equivalence and IFEval instruction checking are explicit dependencies: this
module never substitutes string equality or a partial instruction registry.

The math protocol accepts a closed box even in a length-limited response; the
IFEval protocol requires a closing thinking tag when thinking is enabled.
Neither an incorrect nor a missing answer is an infrastructure failure.
"""

from __future__ import annotations

import importlib
import statistics
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal, Protocol

FinishReason = Literal["stop", "length"]
MathVerifier = Callable[[str, str], bool]


class ScorerInfrastructureError(RuntimeError):
    """A scorer/engine failure that must not be turned into a model's zero score."""


class ScorerUnavailableError(ScorerInfrastructureError):
    """A required benchmark checker has not been installed or configured."""


@dataclass(frozen=True, slots=True)
class MathGrade:
    score: int
    prediction: str | None
    finish_reason: FinishReason


@dataclass(frozen=True, slots=True)
class IFEvalInstruction:
    instruction_id: str
    kwargs: Mapping[str, object]

    def __post_init__(self) -> None:
        if not self.instruction_id.strip():
            raise ValueError("instruction_id must be non-empty")
        if any(not isinstance(key, str) for key in self.kwargs):
            raise ValueError("instruction kwargs must have string keys")


class InstructionChecker(Protocol):
    """The public interface exposed by the official IFEval instruction classes."""

    def build_description(self, **kwargs: object) -> str: ...

    def get_instruction_args(self) -> Sequence[str] | None: ...

    def check_following(self, value: str) -> bool: ...


InstructionRegistry = Mapping[str, Callable[[str], InstructionChecker]]


@dataclass(frozen=True, slots=True)
class IFEvalGrade:
    score: int
    response: str
    instruction_results: tuple[bool, ...]
    finish_reason: FinishReason


@dataclass(frozen=True, slots=True)
class ScoredSample:
    problem_id: str
    sample_id: str
    score: int

    def __post_init__(self) -> None:
        if not self.problem_id.strip() or not self.sample_id.strip():
            raise ValueError("problem_id and sample_id must be non-empty")
        if type(self.score) is not int or self.score not in (0, 1):
            raise ValueError("sample score must be integer zero or one")


@dataclass(frozen=True, slots=True)
class SampledAccuracy:
    mean_accuracy: float
    problem_scores: tuple[tuple[str, float], ...]
    n_problems: int
    n_samples: int


def final_response(text: str, *, thinking: bool) -> str:
    """Return the strict IFEval response, excluding all completed reasoning."""
    if "</think>" in text:
        return text.rsplit("</think>", 1)[1].strip()
    return "" if thinking else text.strip()


def extract_math_answer(text: str) -> str | None:
    r"""Read the source protocol's last ``\boxed`` (or fallback ``\fbox``).

    A last unclosed/empty box invalidates an earlier answer. The source prefers
    any ``\boxed`` over ``\fbox`` even if the latter occurs later. Its math
    path strips a completed thinking block but does not require one to close.
    """
    if "</think>" in text:
        text = text.rsplit("</think>", 1)[1].strip()
    start = text.rfind("\\boxed")
    marker = "\\boxed"
    if start < 0:
        start, marker = text.rfind("\\fbox"), "\\fbox"
    if start < 0:
        return None
    tail = text[start + len(marker):]
    body = tail.lstrip()
    if body.startswith("{"):
        depth = 1
        for index, char in enumerate(body[1:], 1):
            depth += (char == "{") - (char == "}")
            if depth == 0:
                return body[1:index].strip() or None
        return None
    # Upstream accepts a space-delimited unbraced box up to '$' or a newline.
    if marker == "\\boxed" and tail.startswith(" "):
        return tail.strip().split("$", 1)[0].split("\n", 1)[0].strip() or None
    return None


def math_verify_equivalent(prediction: str, gold: str) -> bool:
    r"""Use installed math-verify on ``\boxed{...}`` strings, without fallback.

    Run in a main-thread evaluator process so math-verify's signal-based
    timeouts remain effective. A missing/invalid gold is infrastructure/data
    failure; an unparseable prediction is an ordinary wrong answer.
    """
    if threading.current_thread() is not threading.main_thread():
        raise ScorerInfrastructureError("math-verify must run in a main-thread evaluator process with timeouts")
    try:
        verifier = importlib.import_module("math_verify")
    except ImportError as exc:
        raise ScorerUnavailableError("math-verify is required; install a pinned verifier or inject one explicitly") from exc
    try:
        expected = verifier.parse("\\boxed{" + gold + "}", parsing_timeout=5)
        if not expected:
            raise ScorerInfrastructureError("math-verify could not parse the gold answer")
        predicted = verifier.parse("\\boxed{" + prediction + "}", parsing_timeout=5)
        if not predicted:
            return False
        return bool(verifier.verify(expected, predicted, timeout_seconds=5))
    except ScorerInfrastructureError:
        raise
    except Exception as exc:
        raise ScorerInfrastructureError(f"math-verify failed: {type(exc).__name__}") from exc


def _require_finish(finish_reason: str) -> FinishReason:
    if finish_reason not in ("stop", "length"):
        raise ScorerInfrastructureError(f"generation did not finish normally: {finish_reason!r}")
    return "stop" if finish_reason == "stop" else "length"


def score_math(
    text: str,
    gold: str,
    *,
    verifier: MathVerifier = math_verify_equivalent,
    finish_reason: str = "stop",
) -> MathGrade:
    """Score a sample; missing/wrong answers remain zero-score observations."""
    finish = _require_finish(finish_reason)
    if not gold.strip():
        raise ScorerInfrastructureError("gold answer must be non-empty")
    prediction = extract_math_answer(text)
    if prediction is None:
        return MathGrade(0, None, finish)
    try:
        correct = verifier(prediction, gold)
        if type(correct) is not bool:
            raise TypeError("math verifier must return bool")
    except ScorerInfrastructureError:
        raise
    except Exception as exc:
        raise ScorerInfrastructureError(f"math verifier failed: {type(exc).__name__}") from exc
    return MathGrade(int(correct), prediction, finish)


def score_ifeval(
    text: str,
    instructions: Sequence[IFEvalInstruction],
    *,
    prompt: str,
    registry: InstructionRegistry | None = None,
    thinking: bool = True,
    finish_reason: str = "stop",
) -> IFEvalGrade:
    """Strict prompt-level IFEval: every requested instruction must succeed.

    Supply the official ``INSTRUCTION_DICT`` from a pinned checker installation.
    Fixtures may inject test checkers; those fixtures do not implement IFEval.
    Unknown instructions and checker errors invalidate evaluation, not the model.
    """
    finish = _require_finish(finish_reason)
    if not instructions:
        raise ScorerInfrastructureError("IFEval requires at least one instruction")
    if registry is None:
        raise ScorerUnavailableError("a pinned official IFEval instruction registry is required")
    response = final_response(text, thinking=thinking)
    results: list[bool] = []
    for instruction in instructions:
        try:
            checker = registry[instruction.instruction_id](instruction.instruction_id)
            checker.build_description(**{key: value for key, value in instruction.kwargs.items() if value is not None})
            arguments = checker.get_instruction_args()
            if arguments and "prompt" in arguments:
                checker.build_description(prompt=prompt)
            following = checker.check_following(response) if response else False
            if type(following) is not bool:
                raise TypeError("instruction checker must return bool")
            results.append(following)
        except Exception as exc:
            raise ScorerInfrastructureError(
                f"IFEval checker {instruction.instruction_id!r} failed: {type(exc).__name__}"
            ) from exc
    return IFEvalGrade(int(all(results)), response, tuple(results), finish)


def grouped_sample_accuracy(samples: Sequence[ScoredSample], *, samples_per_problem: int) -> SampledAccuracy:
    """Average within each problem, then across problems; never compute pass@k.

    Require the declared sample count for every present problem. The caller
    must also check that this problem set equals the locked evaluation cohort.
    Infrastructure failures must be reconciled before constructing this input.
    """
    if type(samples_per_problem) is not int or samples_per_problem < 1:
        raise ValueError("samples_per_problem must be a positive integer")
    if not samples:
        raise ValueError("cannot score an empty cohort")
    grouped: dict[str, list[int]] = {}
    seen: set[tuple[str, str]] = set()
    for sample in samples:
        key = (sample.problem_id, sample.sample_id)
        if key in seen:
            raise ValueError(f"duplicate sample identity: {key!r}")
        seen.add(key)
        grouped.setdefault(sample.problem_id, []).append(sample.score)
    if any(len(scores) != samples_per_problem for scores in grouped.values()):
        raise ValueError("incomplete or surplus samples for a problem")
    problem_scores = tuple((problem, statistics.fmean(scores)) for problem, scores in sorted(grouped.items()))
    return SampledAccuracy(
        mean_accuracy=statistics.fmean(score for _, score in problem_scores),
        problem_scores=problem_scores,
        n_problems=len(grouped),
        n_samples=len(samples),
    )
