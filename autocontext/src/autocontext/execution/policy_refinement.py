"""PolicyRefinementLoop — iterative code-policy synthesis.

Iteratively improves a Python code policy for a scenario by:
1. Evaluating the current policy with zero-LLM match execution (PolicyExecutor)
2. Using an LLM to synthesize an improved policy based on match results
3. Repeating until convergence or max iterations

Each refinement iteration costs exactly one LLM call to generate improved code.
Match execution is pure Python against the scenario — no LLM calls.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from autocontext.execution.policy_executor import PolicyExecutor, PolicyMatchResult
from autocontext.providers.base import LLMProvider
from autocontext.scenarios.base import ScenarioInterface

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class PolicyIteration:
    """Record of a single refinement iteration."""

    iteration: int
    policy_source: str
    scores: list[float]
    heuristic_value: float
    had_illegal_actions: bool
    errors: list[str]


@dataclass(frozen=True, slots=True)
class PolicyRefinementResult:
    """Final result from the refinement loop."""

    best_policy: str
    best_heuristic: float
    iterations: int
    converged: bool
    iteration_log: list[PolicyIteration]
    total_matches_run: int


def compute_heuristic(match_results: list[PolicyMatchResult]) -> float:
    """Compute the modified heuristic from match results.

    H = 0.0 if any match had illegal actions or errors (crashed).
    H = 0.5 + 0.5 * avg(normalized_score) otherwise.
    """
    for r in match_results:
        if r.had_illegal_actions or r.errors:
            return 0.0
    if not match_results:
        return 0.0
    avg = sum(r.normalized_score for r in match_results) / len(match_results)
    return 0.5 + 0.5 * avg


def _extract_policy_from_response(response_text: str) -> str:
    """Extract Python policy code from an LLM response.

    Tries to find a fenced code block first, then falls back to the
    full response text.
    """
    # Try to extract from ```python ... ``` blocks
    pattern = r"```(?:python)?\s*\n(.*?)```"
    found: list[str] = re.findall(pattern, response_text, re.DOTALL)
    if found:
        # Use the last code block (most likely the refined policy)
        return str(found[-1]).strip()
    # Fall back to full response
    return response_text.strip()


def _build_refinement_prompt(
    scenario: ScenarioInterface,
    current_policy: str,
    match_results: list[PolicyMatchResult],
    heuristic_value: float,
    iteration: int,
) -> tuple[str, str]:
    """Build (system_prompt, user_prompt) for the LLM refinement call."""
    system_prompt = (
        "You are a Python policy optimization expert. "
        "You write choose_action(state) -> dict functions that play game scenarios well. "
        "You must output a complete Python function definition.\n\n"
        f"Scenario rules:\n{scenario.describe_rules()}\n\n"
        f"Strategy interface:\n{scenario.describe_strategy_interface()}\n\n"
        f"Evaluation criteria:\n{scenario.describe_evaluation_criteria()}"
    )

    scores_str = ", ".join(f"{r.score:.4f}" for r in match_results)
    errors_str = ""
    for r in match_results:
        if r.errors:
            errors_str += f"\nErrors: {'; '.join(r.errors)}"
        if r.had_illegal_actions:
            errors_str += f"\nIllegal actions: {r.illegal_action_count}"

    user_prompt = (
        f"Iteration {iteration}. The current policy achieved heuristic {heuristic_value:.4f}.\n"
        f"Match scores: [{scores_str}]\n"
        f"{errors_str}\n\n"
        f"Current policy:\n```python\n{current_policy}\n```\n\n"
        "Write an improved choose_action(state) -> dict function. "
        "Output ONLY the Python code in a ```python``` code block. "
        "The function must return a dict compatible with the scenario's strategy interface. "
        "Do not use import statements — math, collections, and re are pre-injected."
    )

    return system_prompt, user_prompt


class PolicyRefinementLoop:
    """Iteratively refines a Python code policy for a scenario.

    Each iteration:
    1. Execute matches with PolicyExecutor (zero LLM calls)
    2. Compute heuristic: H=0 on illegality, H=0.5+0.5*avg otherwise
    3. Call LLM once to synthesize improved policy
    4. Repeat with improved policy

    Best policy across all iterations is returned (not just the last).
    """

    def __init__(
        self,
        scenario: ScenarioInterface,
        executor: PolicyExecutor,
        provider: LLMProvider,
        *,
        max_iterations: int = 50,
        matches_per_iteration: int = 5,
        convergence_window: int = 5,
        convergence_epsilon: float = 0.01,
        model: str = "",
    ) -> None:
        self._scenario = scenario
        self._executor = executor
        self._provider = provider
        self._max_iterations = max(1, max_iterations)
        self._matches_per_iteration = max(1, matches_per_iteration)
        self._evaluation_seeds = list(range(self._matches_per_iteration))
        self._convergence_window = max(2, convergence_window)
        self._convergence_epsilon = convergence_epsilon
        self._model = model

    def _evaluate_policy(self, policy_source: str, iteration: int) -> tuple[list[PolicyMatchResult], float]:
        """Evaluate a policy by running matches and computing heuristic."""
        del iteration  # evaluation uses a fixed seed set per refinement run
        results = self._executor.execute_batch(policy_source, seeds=list(self._evaluation_seeds))
        heuristic = compute_heuristic(results)
        return results, heuristic

    def _refine_policy(
        self,
        current_policy: str,
        match_results: list[PolicyMatchResult],
        heuristic_value: float,
        iteration: int,
    ) -> str:
        """Call LLM to generate an improved policy."""
        system_prompt, user_prompt = _build_refinement_prompt(
            self._scenario, current_policy, match_results, heuristic_value, iteration,
        )
        result = self._provider.complete(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            model=self._model or None,
            temperature=0.2,
        )
        return _extract_policy_from_response(result.text)

    def _check_convergence(self, heuristic_history: list[float]) -> bool:
        """Check if heuristic values have converged within the window."""
        if len(heuristic_history) < self._convergence_window:
            return False
        window = heuristic_history[-self._convergence_window:]
        return (max(window) - min(window)) < self._convergence_epsilon

    def refine(self, initial_policy: str) -> PolicyRefinementResult:
        """Run the iterative refinement loop.

        Args:
            initial_policy: Python source code defining the initial
                ``choose_action(state) -> dict`` function.

        Returns:
            PolicyRefinementResult with best policy, heuristic, and full log.
        """
        current_policy = initial_policy
        best_policy = initial_policy
        best_heuristic = 0.0
        iteration_log: list[PolicyIteration] = []
        heuristic_history: list[float] = []
        total_matches = 0
        converged = False

        for i in range(1, self._max_iterations + 1):
            LOGGER.info("policy refinement iteration %d/%d", i, self._max_iterations)

            # Evaluate current policy (zero LLM calls)
            match_results, heuristic = self._evaluate_policy(current_policy, i)
            total_matches += len(match_results)

            # Record iteration
            had_illegal = any(r.had_illegal_actions for r in match_results)
            errors: list[str] = []
            for r in match_results:
                errors.extend(r.errors)

            iteration_entry = PolicyIteration(
                iteration=i,
                policy_source=current_policy,
                scores=[r.score for r in match_results],
                heuristic_value=heuristic,
                had_illegal_actions=had_illegal,
                errors=errors,
            )
            iteration_log.append(iteration_entry)
            heuristic_history.append(heuristic)

            LOGGER.info(
                "iteration %d: heuristic=%.4f, illegal=%s, scores=%s",
                i, heuristic, had_illegal,
                [f"{r.score:.4f}" for r in match_results],
            )

            # Track best
            if heuristic > best_heuristic:
                best_heuristic = heuristic
                best_policy = current_policy

            # Early stop: perfect heuristic
            if heuristic >= 1.0:
                LOGGER.info("perfect heuristic reached at iteration %d", i)
                converged = True
                break

            # Convergence check
            if self._check_convergence(heuristic_history):
                LOGGER.info("convergence detected at iteration %d", i)
                converged = True
                break

            # Refine policy with LLM (1 call per iteration)
            if i < self._max_iterations:
                new_policy = self._refine_policy(current_policy, match_results, heuristic, i)
                current_policy = new_policy

        return PolicyRefinementResult(
            best_policy=best_policy,
            best_heuristic=best_heuristic,
            iterations=len(iteration_log),
            converged=converged,
            iteration_log=iteration_log,
            total_matches_run=total_matches,
        )
