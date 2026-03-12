"""A/B testing framework for AutoContext configuration comparison.

Inspired by Plankton's SWE-bench A/B testing with McNemar's test,
randomized condition order, and abort criteria.
"""
from __future__ import annotations

import os
import random
from dataclasses import dataclass, field

from autocontext.config.settings import load_settings
from autocontext.loop.generation_runner import GenerationRunner, RunSummary


@dataclass(slots=True)
class ABTestConfig:
    """Configuration for an A/B test run."""

    scenario: str
    baseline_env: dict[str, str]
    treatment_env: dict[str, str]
    runs_per_condition: int = 5
    generations_per_run: int = 3
    seed: int = 42


@dataclass(slots=True)
class ABTestResult:
    """Paired results from an A/B test."""

    baseline_scores: list[float] = field(default_factory=list)
    treatment_scores: list[float] = field(default_factory=list)
    baseline_elos: list[float] = field(default_factory=list)
    treatment_elos: list[float] = field(default_factory=list)

    def mean_delta(self) -> float:
        """Treatment mean minus baseline mean."""
        if not self.baseline_scores or not self.treatment_scores:
            return 0.0
        b_mean = sum(self.baseline_scores) / len(self.baseline_scores)
        t_mean = sum(self.treatment_scores) / len(self.treatment_scores)
        return t_mean - b_mean

    def treatment_wins(self) -> int:
        """Count of runs where treatment outscored baseline."""
        return sum(1 for t, b in zip(self.treatment_scores, self.baseline_scores, strict=True) if t > b)

    def baseline_wins(self) -> int:
        """Count of runs where baseline outscored treatment."""
        return sum(1 for t, b in zip(self.treatment_scores, self.baseline_scores, strict=True) if b > t)


class ABTestRunner:
    """Runs paired A/B tests comparing two AutoContext configurations."""

    def __init__(self, config: ABTestConfig) -> None:
        self._config = config

    def run(self) -> ABTestResult:
        """Execute the A/B test with randomized condition order."""
        result = ABTestResult()
        rng = random.Random(self._config.seed)

        for i in range(self._config.runs_per_condition):
            baseline_first = rng.random() < 0.5

            if baseline_first:
                b_summary = self._run_condition(self._config.baseline_env, f"ab_baseline_{i}")
                t_summary = self._run_condition(self._config.treatment_env, f"ab_treatment_{i}")
            else:
                t_summary = self._run_condition(self._config.treatment_env, f"ab_treatment_{i}")
                b_summary = self._run_condition(self._config.baseline_env, f"ab_baseline_{i}")

            result.baseline_scores.append(b_summary.best_score)
            result.treatment_scores.append(t_summary.best_score)
            result.baseline_elos.append(b_summary.current_elo)
            result.treatment_elos.append(t_summary.current_elo)

        return result

    def _run_condition(self, env_overrides: dict[str, str], run_id: str) -> RunSummary:
        """Run a single condition with environment overrides.

        Env vars are set only during ``load_settings()`` then restored
        immediately, before ``runner.run()`` starts threads.  This avoids
        thread-unsafe mutation of ``os.environ`` during execution.
        """
        original_env: dict[str, str | None] = {}
        for k, v in env_overrides.items():
            original_env[k] = os.environ.get(k)
            os.environ[k] = v

        try:
            settings = load_settings()
        finally:
            # Restore env BEFORE runner.run() starts threads
            for k, orig in original_env.items():
                if orig is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = orig

        runner = GenerationRunner(settings)
        return runner.run(
            scenario_name=self._config.scenario,
            generations=self._config.generations_per_run,
            run_id=run_id,
        )
