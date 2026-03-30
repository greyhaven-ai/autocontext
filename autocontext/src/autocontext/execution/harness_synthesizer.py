"""HarnessSynthesizer — iterative LLM refinement loop for harness code.

Generates an initial harness from a scenario description, tests it against
diverse sample states, collects failures, and asks an LLM to refine until
accuracy reaches the target or the iteration budget is exhausted.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

from autocontext.execution.harness_tester import HarnessTester
from autocontext.execution.sample_states import SampleState
from autocontext.providers.base import LLMProvider
from autocontext.scenarios.base import ScenarioInterface

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SynthesisResult:
    """Outcome of the harness synthesis loop."""

    harness_source: str
    iterations: int
    accuracy: float
    converged: bool
    failure_log: list[str]


class HarnessSynthesizer:
    """Iteratively synthesize harness code using an LLM.

    Parameters
    ----------
    scenario:
        The scenario whose rules define what valid actions and states look like.
    provider:
        The LLM provider used to generate and refine harness code.
    max_iterations:
        Maximum number of generate-test-refine cycles (default 30).
    accuracy_target:
        Stop early once accuracy reaches this threshold (default 1.0).
    model:
        LLM model to use for generation (default ``"haiku"``).
    """

    def __init__(
        self,
        scenario: ScenarioInterface,
        provider: LLMProvider,
        *,
        max_iterations: int = 30,
        accuracy_target: float = 1.0,
        model: str = "haiku",
    ) -> None:
        self._scenario = scenario
        self._provider = provider
        self._max_iterations = max_iterations
        self._accuracy_target = accuracy_target
        self._model = model
        self._tester = HarnessTester(max_failures_reported=5, timeout_per_test=2.0)

    def synthesize(
        self,
        sample_states: list[SampleState],
        target_functions: list[str] | None = None,
        output_dir: Path | None = None,
    ) -> SynthesisResult:
        """Run the iterative synthesis loop.

        Parameters
        ----------
        sample_states:
            States to test against (from ``SampleStateGenerator``).
        target_functions:
            Function names the harness must define
            (default: ``["validate_strategy", "enumerate_legal_actions", "is_legal_action"]``).
        output_dir:
            If provided, write the final harness to this directory.

        Returns
        -------
        SynthesisResult
            Contains the best harness source, accuracy, and iteration metadata.
        """
        if target_functions is None:
            target_functions = ["validate_strategy", "enumerate_legal_actions", "is_legal_action"]

        failure_log: list[str] = []
        best_source = ""
        best_accuracy = -1.0
        current_source = ""

        for iteration in range(1, self._max_iterations + 1):
            # ── Generate or refine ────────────────────────────────────────
            if iteration == 1:
                current_source = self._generate_initial(target_functions)
            else:
                current_source = self._refine(current_source, failure_log[-1] if failure_log else "", target_functions)

            # ── Extract code from LLM response ────────────────────────────
            extracted = _extract_python_code(current_source)
            if extracted:
                current_source = extracted

            # ── Test ──────────────────────────────────────────────────────
            report = self._tester.test_harness(
                current_source,
                sample_states,
                scenario=self._scenario,
                required_functions=target_functions,
            )

            logger.info(
                "synthesis iteration %d: accuracy=%.2f (%d/%d passed)",
                iteration, report.accuracy, report.passed, report.total_tests,
            )

            if report.accuracy > best_accuracy:
                best_accuracy = report.accuracy
                best_source = current_source

            if report.accuracy >= self._accuracy_target:
                if output_dir is not None:
                    self._write_output(current_source, output_dir)
                return SynthesisResult(
                    harness_source=current_source,
                    iterations=iteration,
                    accuracy=report.accuracy,
                    converged=True,
                    failure_log=failure_log,
                )

            # ── Log failures for next refinement ──────────────────────────
            failure_summaries: list[str] = []
            for f in report.failures:
                failure_summaries.append(
                    f"[{f.function_name}] state={f.state_description}: {f.error}"
                )
            log_entry = (
                f"iter {iteration}: accuracy={report.accuracy:.2f}, "
                f"failures={report.failed}/{report.total_tests}"
            )
            if failure_summaries:
                log_entry += "\n  " + "\n  ".join(failure_summaries)
            failure_log.append(log_entry)

        # ── Budget exhausted ──────────────────────────────────────────────
        if output_dir is not None:
            self._write_output(best_source, output_dir)

        return SynthesisResult(
            harness_source=best_source,
            iterations=self._max_iterations,
            accuracy=best_accuracy,
            converged=False,
            failure_log=failure_log,
        )

    # ── Prompt construction ───────────────────────────────────────────────

    def _generate_initial(self, target_functions: list[str]) -> str:
        """Ask the LLM to produce the first version of the harness."""
        system_prompt = (
            "You are a Python code generator. Generate ONLY valid Python code "
            "with no imports. The code must define plain Python functions using "
            "only safe builtins (abs, all, any, bool, dict, enumerate, filter, "
            "float, frozenset, int, isinstance, issubclass, len, list, map, max, "
            "min, print, range, repr, reversed, round, set, sorted, str, sum, "
            "tuple, zip). No import statements allowed."
        )
        func_specs = "\n".join(f"- {fn}" for fn in target_functions)
        user_prompt = (
            f"Generate a Python harness for the '{self._scenario.name}' scenario.\n\n"
            f"Scenario rules:\n{self._scenario.describe_rules()}\n\n"
            f"Strategy interface:\n{self._scenario.describe_strategy_interface()}\n\n"
            f"Required functions:\n{func_specs}\n\n"
            "Function signatures:\n"
            "- validate_strategy(strategy: dict, scenario) -> tuple[bool, list[str]]\n"
            "- enumerate_legal_actions(state: dict) -> list[dict]\n"
            "- is_legal_action(state: dict, action: dict) -> bool\n\n"
            "Return ONLY the Python code, no explanation."
        )
        result = self._provider.complete(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            model=self._model,
            temperature=0.0,
        )
        return result.text

    def _refine(self, current_source: str, failure_context: str, target_functions: list[str]) -> str:
        """Ask the LLM to fix the harness based on test failures."""
        system_prompt = (
            "You are a Python code fixer. Fix the provided code based on the "
            "test failures. Return ONLY valid Python code with no imports. "
            "Only safe builtins are available."
        )
        func_specs = "\n".join(f"- {fn}" for fn in target_functions)
        user_prompt = (
            f"The following harness code for '{self._scenario.name}' has failures:\n\n"
            f"```python\n{current_source}\n```\n\n"
            f"Test failures:\n{failure_context}\n\n"
            f"Scenario rules:\n{self._scenario.describe_rules()}\n\n"
            f"Strategy interface:\n{self._scenario.describe_strategy_interface()}\n\n"
            f"Required functions:\n{func_specs}\n\n"
            "Fix the code and return ONLY the corrected Python code."
        )
        result = self._provider.complete(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            model=self._model,
            temperature=0.0,
        )
        return result.text

    # ── Output ────────────────────────────────────────────────────────────

    @staticmethod
    def _write_output(source: str, output_dir: Path) -> None:
        """Write the harness source to the output directory."""
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / "synthesized_harness.py"
        output_path.write_text(source, encoding="utf-8")
        logger.info("wrote synthesized harness to %s", output_path)


def _extract_python_code(text: str) -> str | None:
    """Extract Python code from a fenced code block if present."""
    match = re.search(r"```(?:python)?\s*\n(.*?)```", text, re.DOTALL)
    if match:
        return match.group(1).strip()
    # If the text looks like raw Python (starts with def/class), return as-is
    stripped = text.strip()
    if stripped.startswith("def ") or stripped.startswith("class "):
        return stripped
    return None
