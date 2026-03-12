"""HarnessTester — validates candidate harness code against sample states in parallel.

Runs each harness function (``enumerate_legal_actions``, ``is_legal_action``,
``validate_strategy``) against a collection of :class:`SampleState` objects,
collecting structured failure reports suitable for LLM-driven refinement.
"""
from __future__ import annotations

import ast
import json
import logging
import time
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
from dataclasses import dataclass
from typing import Any

from autocontext.execution.ast_safety import check_ast_safety
from autocontext.execution.harness_loader import _SAFE_BUILTINS, _exec_harness_source
from autocontext.execution.sample_states import SampleState

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class HarnessTestFailure:
    """Structured failure from a single harness test."""

    state: dict[str, Any]
    function_name: str
    expected: Any
    actual: Any
    error: str
    state_description: str


@dataclass(frozen=True, slots=True)
class HarnessTestReport:
    """Aggregate results from testing a harness against sample states."""

    total_tests: int
    passed: int
    failed: int
    accuracy: float
    failures: list[HarnessTestFailure]
    execution_time_ms: float


class HarnessTester:
    """Test candidate harness code against sample states in parallel.

    Parameters
    ----------
    parallel_workers:
        Number of threads for parallel test execution (default 10).
    timeout_per_test:
        Max seconds per individual test invocation (default 2.0).
    max_failures_reported:
        Maximum number of failure details to keep (default 5).
        Failures are sampled for phase diversity.
    """

    def __init__(
        self,
        *,
        parallel_workers: int = 10,
        timeout_per_test: float = 2.0,
        max_failures_reported: int = 5,
    ) -> None:
        self._parallel_workers = parallel_workers
        self._timeout_per_test = timeout_per_test
        self._max_failures_reported = max_failures_reported

    def test_harness(
        self,
        harness_source: str,
        sample_states: list[SampleState],
        *,
        scenario: Any | None = None,
        required_functions: list[str] | None = None,
    ) -> HarnessTestReport:
        """Run *harness_source* against every state and return a structured report."""
        t0 = time.monotonic()

        if not sample_states:
            return HarnessTestReport(
                total_tests=0, passed=0, failed=0, accuracy=1.0,
                failures=[], execution_time_ms=0.0,
            )

        # ── Pre-flight: syntax + AST safety ──────────────────────────────
        try:
            ast.parse(harness_source)
        except SyntaxError as exc:
            elapsed_ms = (time.monotonic() - t0) * 1000
            failures = self._make_blanket_failures(
                sample_states, "parse", f"syntax error: {exc}",
            )
            return HarnessTestReport(
                total_tests=len(sample_states),
                passed=0,
                failed=len(sample_states),
                accuracy=0.0,
                failures=failures,
                execution_time_ms=elapsed_ms,
            )

        violations = check_ast_safety(harness_source)
        if violations:
            elapsed_ms = (time.monotonic() - t0) * 1000
            msg = f"AST safety violations: {'; '.join(violations)}"
            failures = self._make_blanket_failures(sample_states, "ast_safety", msg)
            return HarnessTestReport(
                total_tests=len(sample_states),
                passed=0,
                failed=len(sample_states),
                accuracy=0.0,
                failures=failures,
                execution_time_ms=elapsed_ms,
            )

        # ── Load harness into a namespace ────────────────────────────────
        namespace: dict[str, Any] = {"__builtins__": dict(_SAFE_BUILTINS)}
        try:
            _exec_harness_source(harness_source, namespace)
        except Exception as exc:
            elapsed_ms = (time.monotonic() - t0) * 1000
            failures = self._make_blanket_failures(
                sample_states, "load", f"harness load error: {exc}",
            )
            return HarnessTestReport(
                total_tests=len(sample_states),
                passed=0,
                failed=len(sample_states),
                accuracy=0.0,
                failures=failures,
                execution_time_ms=elapsed_ms,
            )

        # Extract callable functions
        fn_enumerate = namespace.get("enumerate_legal_actions")
        fn_is_legal = namespace.get("is_legal_action")
        fn_validate = namespace.get("validate_strategy")
        callables: dict[str, Any] = {
            "enumerate_legal_actions": fn_enumerate if callable(fn_enumerate) else None,
            "is_legal_action": fn_is_legal if callable(fn_is_legal) else None,
            "validate_strategy": fn_validate if callable(fn_validate) else None,
        }

        missing = [name for name in (required_functions or []) if callables.get(name) is None]
        if missing:
            elapsed_ms = (time.monotonic() - t0) * 1000
            failures = self._make_blanket_failures(
                sample_states,
                "missing_function",
                f"missing required harness function(s): {', '.join(missing)}",
            )
            return HarnessTestReport(
                total_tests=len(sample_states),
                passed=0,
                failed=len(sample_states),
                accuracy=0.0,
                failures=failures,
                execution_time_ms=elapsed_ms,
            )

        # ── Run tests in parallel ────────────────────────────────────────
        all_failures: list[HarnessTestFailure] = []
        passed = 0

        pool = ThreadPoolExecutor(max_workers=self._parallel_workers)
        futures: dict[Future[HarnessTestFailure | None], SampleState] = {
            pool.submit(
                _test_single_state,
                sample,
                fn_enumerate=callables["enumerate_legal_actions"],
                fn_is_legal=callables["is_legal_action"],
                fn_validate=callables["validate_strategy"],
                scenario=scenario,
            ): sample
            for sample in sample_states
        }

        for future in futures:
            try:
                result = future.result(timeout=self._timeout_per_test)
            except FuturesTimeoutError:
                sample = futures[future]
                all_failures.append(HarnessTestFailure(
                    state=sample.state,
                    function_name="(overall)",
                    expected=None,
                    actual=None,
                    error="timed out",
                    state_description=sample.description,
                ))
                continue
            except Exception as exc:
                sample = futures[future]
                all_failures.append(HarnessTestFailure(
                    state=sample.state,
                    function_name="(overall)",
                    expected=None,
                    actual=None,
                    error=f"unexpected error: {exc}",
                    state_description=sample.description,
                ))
                continue

            if result is None:
                passed += 1
            else:
                all_failures.append(result)

        # Shut down without waiting for hung threads (daemon threads will die with process)
        pool.shutdown(wait=False, cancel_futures=True)

        failed = len(all_failures)
        total = len(sample_states)
        accuracy = (total - failed) / total if total > 0 else 1.0
        elapsed_ms = (time.monotonic() - t0) * 1000

        # ── Sample diverse failures ──────────────────────────────────────
        sampled = self._sample_diverse_failures(all_failures)

        return HarnessTestReport(
            total_tests=total,
            passed=passed,
            failed=failed,
            accuracy=accuracy,
            failures=sampled,
            execution_time_ms=elapsed_ms,
        )

    # ── Internal helpers ──────────────────────────────────────────────────

    def _sample_diverse_failures(self, failures: list[HarnessTestFailure]) -> list[HarnessTestFailure]:
        """Sample up to ``max_failures_reported`` failures with phase diversity."""
        if len(failures) <= self._max_failures_reported:
            return list(failures)

        # Group by phase (extracted from state_description) and error type
        by_phase: dict[str, list[HarnessTestFailure]] = {}
        for f in failures:
            phase = "unknown"
            for p in ("early", "mid", "late"):
                if p in f.state_description.lower():
                    phase = p
                    break
            by_phase.setdefault(phase, []).append(f)

        # Also group by function_name for error diversity
        by_func: dict[str, list[HarnessTestFailure]] = {}
        for f in failures:
            by_func.setdefault(f.function_name, []).append(f)

        sampled: list[HarnessTestFailure] = []
        seen_descriptions: set[str] = set()

        # Round-robin: one from each phase first
        for phase_failures in by_phase.values():
            if len(sampled) >= self._max_failures_reported:
                break
            for f in phase_failures:
                if f.state_description not in seen_descriptions:
                    sampled.append(f)
                    seen_descriptions.add(f.state_description)
                    break

        # Then fill with error type diversity
        for func_failures in by_func.values():
            if len(sampled) >= self._max_failures_reported:
                break
            for f in func_failures:
                if f.state_description not in seen_descriptions:
                    sampled.append(f)
                    seen_descriptions.add(f.state_description)
                    break

        # If still under limit, fill from remaining
        for f in failures:
            if len(sampled) >= self._max_failures_reported:
                break
            if f.state_description not in seen_descriptions:
                sampled.append(f)
                seen_descriptions.add(f.state_description)

        return sampled

    def _make_blanket_failures(
        self,
        states: list[SampleState],
        function_name: str,
        error: str,
    ) -> list[HarnessTestFailure]:
        """Create failures for all states (used for parse/safety errors)."""
        all_failures = [
            HarnessTestFailure(
                state=s.state,
                function_name=function_name,
                expected=None,
                actual=None,
                error=error,
                state_description=s.description,
            )
            for s in states
        ]
        return self._sample_diverse_failures(all_failures)


def _test_single_state(
    sample: SampleState,
    *,
    fn_enumerate: Any | None,
    fn_is_legal: Any | None,
    fn_validate: Any | None,
    scenario: Any | None,
) -> HarnessTestFailure | None:
    """Test all available harness functions against a single sample state.

    Returns ``None`` if everything passes, or a ``HarnessTestFailure`` on
    the first detected problem.  This is a module-level function so it can
    be submitted to a ``ThreadPoolExecutor`` without nesting pools.
    """
    state = sample.state

    # Test enumerate_legal_actions if available
    if fn_enumerate is not None and sample.expected_legal_actions is not None:
        try:
            actual = fn_enumerate(state)
            if not _actions_match(sample.expected_legal_actions, actual):
                return HarnessTestFailure(
                    state=state,
                    function_name="enumerate_legal_actions",
                    expected=sample.expected_legal_actions,
                    actual=actual,
                    error="return value does not match expected legal actions",
                    state_description=sample.description,
                )
        except Exception as exc:
            return HarnessTestFailure(
                state=state,
                function_name="enumerate_legal_actions",
                expected=sample.expected_legal_actions,
                actual=None,
                error=str(exc),
                state_description=sample.description,
            )
    elif fn_enumerate is not None:
        # No ground truth, just check it doesn't crash
        try:
            fn_enumerate(state)
        except Exception as exc:
            return HarnessTestFailure(
                state=state,
                function_name="enumerate_legal_actions",
                expected=None,
                actual=None,
                error=str(exc),
                state_description=sample.description,
            )

    # Test is_legal_action against ground truth
    if fn_is_legal is not None and sample.expected_legal_actions is not None:
        for action in sample.expected_legal_actions:
            try:
                result = fn_is_legal(state, action)
                if not result:
                    return HarnessTestFailure(
                        state=state,
                        function_name="is_legal_action",
                        expected=True,
                        actual=result,
                        error=f"expected action {action} to be legal but got {result}",
                        state_description=sample.description,
                    )
            except Exception as exc:
                return HarnessTestFailure(
                    state=state,
                    function_name="is_legal_action",
                    expected=True,
                    actual=None,
                    error=str(exc),
                    state_description=sample.description,
                )

    # Test validate_strategy doesn't crash (no ground truth needed)
    if fn_validate is not None:
        try:
            strategy = _example_strategy_from_sample(sample, scenario)
            if strategy is None:
                if scenario is None and sample.expected_legal_actions is None:
                    return None
                return HarnessTestFailure(
                    state=state,
                    function_name="validate_strategy",
                    expected="a valid example strategy",
                    actual=None,
                    error="could not derive an example strategy from sample state",
                    state_description=sample.description,
                )
            result = fn_validate(strategy, scenario)
            if not _validation_result_is_valid(result):
                return HarnessTestFailure(
                    state=state,
                    function_name="validate_strategy",
                    expected="tuple[bool, list[str]]",
                    actual=result,
                    error="validate_strategy returned an invalid result shape",
                    state_description=sample.description,
                )
            passed, errors = result
            if not passed:
                return HarnessTestFailure(
                    state=state,
                    function_name="validate_strategy",
                    expected=True,
                    actual=result,
                    error=f"rejected derived valid strategy: {errors}",
                    state_description=sample.description,
                )
        except Exception as exc:
            return HarnessTestFailure(
                state=state,
                function_name="validate_strategy",
                expected=None,
                actual=None,
                error=str(exc),
                state_description=sample.description,
            )

    return None


def _actions_match(expected: list[dict[str, Any]], actual: Any) -> bool:
    """Compare expected and actual legal action lists."""
    if not isinstance(actual, list):
        return False
    if len(expected) != len(actual):
        return False
    expected_keys = sorted(json.dumps(_normalize_action(a), sort_keys=True) for a in expected)
    actual_keys = sorted(json.dumps(_normalize_action(a), sort_keys=True) for a in actual)
    return expected_keys == actual_keys


def _normalize_action(value: Any) -> Any:
    """Normalize nested action descriptors for stable equality checks."""
    if isinstance(value, dict):
        return {key: _normalize_action(val) for key, val in sorted(value.items())}
    if isinstance(value, list):
        return [_normalize_action(item) for item in value]
    return value


def _validation_result_is_valid(result: Any) -> bool:
    """Check the validate_strategy return contract."""
    return (
        isinstance(result, tuple)
        and len(result) == 2
        and isinstance(result[0], bool)
        and isinstance(result[1], list)
        and all(isinstance(item, str) for item in result[1])
    )


def _example_strategy_from_sample(sample: SampleState, scenario: Any | None) -> dict[str, Any] | None:
    """Derive a valid example strategy from the sample's legal-action metadata."""
    legal_actions = sample.expected_legal_actions
    if legal_actions is None and scenario is not None:
        enumerate_fn = getattr(scenario, "enumerate_legal_actions", None)
        if callable(enumerate_fn):
            enumerated = enumerate_fn(sample.state)
            if isinstance(enumerated, list):
                legal_actions = enumerated
    if not legal_actions:
        return None

    if all(isinstance(action, dict) and action.get("type") == "continuous" and "range" in action for action in legal_actions):
        low_strategy: dict[str, Any] = {}
        midpoint_strategy: dict[str, Any] = {}
        for action in legal_actions:
            low, high = action["range"]
            low_strategy[str(action["action"])] = round(float(low), 4)
            midpoint = (float(low) + float(high)) / 2.0
            midpoint_strategy[str(action["action"])] = round(midpoint, 4)
        for candidate in (midpoint_strategy, low_strategy):
            if _strategy_is_valid(sample, scenario, candidate):
                return candidate
        return None

    first_action = legal_actions[0]
    if isinstance(first_action, dict):
        candidate = dict(first_action)
        if _strategy_is_valid(sample, scenario, candidate):
            return candidate
    return None


def _strategy_is_valid(sample: SampleState, scenario: Any | None, strategy: dict[str, Any]) -> bool:
    """Check whether the example strategy satisfies the scenario contract when available."""
    if scenario is None:
        return True
    validate_fn = getattr(scenario, "validate_actions", None)
    if not callable(validate_fn):
        return True
    valid, _ = validate_fn(sample.state, "harness_tester", strategy)
    return bool(valid)
