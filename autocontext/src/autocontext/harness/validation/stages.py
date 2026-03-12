"""Concrete validation stages and runner with cost tracking.

Each stage is a pure, composable check — no persistence, retries, or event
emission.  The runner orchestrates stages and accumulates metrics.

Stages:
    0. SyntaxStage         — Parse + AST safety (cheap, instant)
    1. ContractStage       — Schema / interface compliance
    2. DeterministicStage  — Fixed-seed consistency
    3. EdgeCaseStage       — Boundary conditions from scenario fixtures
    4. EvaluationReadyStage — Minimum executable check before full evaluation
"""
from __future__ import annotations

import ast
import logging
import time
from typing import Any

from autocontext.harness.validation.staged import (
    StageResult,
    StageStatus,
    ValidationPipeline,
    ValidationStage,
)

LOGGER = logging.getLogger(__name__)
DEFAULT_STAGE_TIMEOUT_SECONDS = 5.0


# ── Concrete stages ──────────────────────────────────────────────────────


class SyntaxStage(ValidationStage):
    """Stage 0: parse candidate as valid JSON/Python/structured data."""

    @property
    def name(self) -> str:
        return "syntax"

    def run(self, candidate: Any, scenario: Any) -> StageResult:
        t0 = time.monotonic()

        if candidate is None:
            return StageResult(
                stage=self.order, name=self.name, status=StageStatus.FAILED,
                duration_ms=_elapsed(t0), error="candidate is None", error_code="invalid_type",
            )

        # Dict / list / number — structurally valid
        if isinstance(candidate, (dict, list, int, float, bool)):
            return StageResult(
                stage=self.order, name=self.name, status=StageStatus.PASSED,
                duration_ms=_elapsed(t0),
            )

        # String — could be Python source or JSON
        if isinstance(candidate, str):
            # Try Python parse + AST safety
            try:
                ast.parse(candidate)
            except SyntaxError as exc:
                return StageResult(
                    stage=self.order, name=self.name, status=StageStatus.FAILED,
                    duration_ms=_elapsed(t0), error=f"syntax error: {exc}",
                    error_code="syntax_error",
                )

            from autocontext.execution.ast_safety import check_ast_safety

            violations = check_ast_safety(candidate)
            if violations:
                return StageResult(
                    stage=self.order, name=self.name, status=StageStatus.FAILED,
                    duration_ms=_elapsed(t0),
                    error=f"AST safety violations: {'; '.join(violations)}",
                    error_code="ast_safety",
                )

            return StageResult(
                stage=self.order, name=self.name, status=StageStatus.PASSED,
                duration_ms=_elapsed(t0),
            )

        return StageResult(
            stage=self.order, name=self.name, status=StageStatus.FAILED,
            duration_ms=_elapsed(t0),
            error=f"unsupported candidate type: {type(candidate).__name__}",
            error_code="invalid_type",
        )


class ContractStage(ValidationStage):
    """Stage 1: check candidate matches the scenario or task interface schema."""

    @property
    def name(self) -> str:
        return "contract"

    def run(self, candidate: Any, scenario: Any) -> StageResult:
        t0 = time.monotonic()

        # Code candidate — must define choose_action
        if isinstance(candidate, str):
            try:
                tree = ast.parse(candidate)
            except SyntaxError:
                # Should have been caught by SyntaxStage, but be defensive
                return StageResult(
                    stage=self.order, name=self.name, status=StageStatus.FAILED,
                    duration_ms=_elapsed(t0), error="cannot parse code candidate",
                    error_code="syntax_error",
                )
            func_names = {
                node.name for node in ast.walk(tree)
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            }
            if "choose_action" not in func_names:
                return StageResult(
                    stage=self.order, name=self.name, status=StageStatus.FAILED,
                    duration_ms=_elapsed(t0),
                    error="code candidate must define choose_action(state)",
                    error_code="missing_entry_point",
                )
            return StageResult(
                stage=self.order, name=self.name, status=StageStatus.PASSED,
                duration_ms=_elapsed(t0),
            )

        # Dict candidate — validate against scenario if available
        if isinstance(candidate, dict) and scenario is not None:
            validate_fn = getattr(scenario, "validate_actions", None)
            if callable(validate_fn):
                initial_state_fn = getattr(scenario, "initial_state", None)
                state = initial_state_fn() if callable(initial_state_fn) else {}
                valid, reason = validate_fn(state, "challenger", candidate)
                if not valid:
                    return StageResult(
                        stage=self.order, name=self.name, status=StageStatus.FAILED,
                        duration_ms=_elapsed(t0),
                        error=f"contract violation: {reason}",
                        error_code="contract_violation",
                    )

        return StageResult(
            stage=self.order, name=self.name, status=StageStatus.PASSED,
            duration_ms=_elapsed(t0),
        )


class DeterministicStage(ValidationStage):
    """Stage 2: execute candidate twice with same seed and compare outputs."""

    def __init__(self, order: int, *, timeout_seconds: float = DEFAULT_STAGE_TIMEOUT_SECONDS) -> None:
        super().__init__(order)
        self._timeout_seconds = timeout_seconds

    @property
    def name(self) -> str:
        return "deterministic"

    def run(self, candidate: Any, scenario: Any) -> StageResult:
        t0 = time.monotonic()

        # Dict strategies are inherently deterministic
        if isinstance(candidate, (dict, list)):
            return StageResult(
                stage=self.order, name=self.name, status=StageStatus.PASSED,
                duration_ms=_elapsed(t0),
            )

        # Code needs a scenario to test determinism
        if isinstance(candidate, str) and scenario is None:
            return StageResult(
                stage=self.order, name=self.name, status=StageStatus.SKIPPED,
                duration_ms=_elapsed(t0),
            )

        if isinstance(candidate, str):
            try:
                fn = _load_choose_action(candidate, timeout_seconds=self._timeout_seconds)
            except TimeoutError:
                return StageResult(
                    stage=self.order, name=self.name, status=StageStatus.FAILED,
                    duration_ms=_elapsed(t0),
                    error="timed out while loading choose_action",
                    error_code="timeout",
                )
            except Exception as exc:
                return StageResult(
                    stage=self.order, name=self.name, status=StageStatus.FAILED,
                    duration_ms=_elapsed(t0),
                    error=f"failed to load code: {exc}",
                    error_code="load_error",
                )

            initial_state_fn = getattr(scenario, "initial_state", None)
            state = initial_state_fn() if callable(initial_state_fn) else {}

            try:
                result1 = _run_choose_action(fn, dict(state), timeout_seconds=self._timeout_seconds)
                result2 = _run_choose_action(fn, dict(state), timeout_seconds=self._timeout_seconds)
            except TimeoutError:
                return StageResult(
                    stage=self.order, name=self.name, status=StageStatus.FAILED,
                    duration_ms=_elapsed(t0),
                    error="timed out while executing choose_action",
                    error_code="timeout",
                )
            except Exception as exc:
                return StageResult(
                    stage=self.order, name=self.name, status=StageStatus.FAILED,
                    duration_ms=_elapsed(t0),
                    error=f"execution error: {exc}",
                    error_code="execution_error",
                )

            if result1 != result2:
                return StageResult(
                    stage=self.order, name=self.name, status=StageStatus.FAILED,
                    duration_ms=_elapsed(t0),
                    error="non-deterministic: different outputs for same input",
                    error_code="non_deterministic",
                )

        return StageResult(
            stage=self.order, name=self.name, status=StageStatus.PASSED,
            duration_ms=_elapsed(t0),
        )


class EdgeCaseStage(ValidationStage):
    """Stage 3: test candidate against scenario-provided edge fixtures."""

    def __init__(self, order: int, *, timeout_seconds: float = DEFAULT_STAGE_TIMEOUT_SECONDS) -> None:
        super().__init__(order)
        self._timeout_seconds = timeout_seconds

    @property
    def name(self) -> str:
        return "edge_case"

    def run(self, candidate: Any, scenario: Any) -> StageResult:
        t0 = time.monotonic()

        # Skip gracefully when no scenario or no edge fixtures
        if scenario is None:
            return StageResult(
                stage=self.order, name=self.name, status=StageStatus.SKIPPED,
                duration_ms=_elapsed(t0),
            )

        get_fixtures = getattr(scenario, "get_edge_fixtures", None)
        if not callable(get_fixtures):
            return StageResult(
                stage=self.order, name=self.name, status=StageStatus.SKIPPED,
                duration_ms=_elapsed(t0),
            )

        fixtures = get_fixtures()
        if not fixtures:
            return StageResult(
                stage=self.order, name=self.name, status=StageStatus.SKIPPED,
                duration_ms=_elapsed(t0),
            )

        validate_fn = getattr(scenario, "validate_actions", None)
        if not callable(validate_fn):
            return StageResult(
                stage=self.order, name=self.name, status=StageStatus.SKIPPED,
                duration_ms=_elapsed(t0),
            )

        choose_action: Any | None = None
        if isinstance(candidate, str):
            try:
                choose_action = _load_choose_action(candidate, timeout_seconds=self._timeout_seconds)
            except TimeoutError:
                return StageResult(
                    stage=self.order, name=self.name, status=StageStatus.FAILED,
                    duration_ms=_elapsed(t0),
                    error="timed out while loading choose_action",
                    error_code="timeout",
                )
            except Exception as exc:
                return StageResult(
                    stage=self.order, name=self.name, status=StageStatus.FAILED,
                    duration_ms=_elapsed(t0),
                    error=f"failed to load code: {exc}",
                    error_code="load_error",
                )

        for fixture in fixtures:
            state = fixture.get("state", {})
            expected_valid = fixture.get("expected_valid", True)
            actions: Any = candidate
            if choose_action is not None:
                try:
                    actions = _run_choose_action(choose_action, dict(state), timeout_seconds=self._timeout_seconds)
                except TimeoutError:
                    return StageResult(
                        stage=self.order, name=self.name, status=StageStatus.FAILED,
                        duration_ms=_elapsed(t0),
                        error=f"timed out while executing choose_action for state={state!r}",
                        error_code="timeout",
                    )
                except Exception as exc:
                    return StageResult(
                        stage=self.order, name=self.name, status=StageStatus.FAILED,
                        duration_ms=_elapsed(t0),
                        error=f"choose_action raised on edge fixture: {exc}",
                        error_code="execution_error",
                    )

                if not isinstance(actions, dict):
                    return StageResult(
                        stage=self.order, name=self.name, status=StageStatus.FAILED,
                        duration_ms=_elapsed(t0),
                        error=f"choose_action must return dict, got {type(actions).__name__}",
                        error_code="invalid_return_type",
                    )

            valid, _reason = validate_fn(state, "challenger", actions)

            if bool(valid) != bool(expected_valid):
                return StageResult(
                    stage=self.order, name=self.name, status=StageStatus.FAILED,
                    duration_ms=_elapsed(t0),
                    error=(
                        f"edge case mismatch: expected valid={expected_valid}, "
                        f"got valid={valid} for state={state!r}"
                    ),
                    error_code="edge_case_mismatch",
                )

        return StageResult(
            stage=self.order, name=self.name, status=StageStatus.PASSED,
            duration_ms=_elapsed(t0),
        )


class EvaluationReadyStage(ValidationStage):
    """Stage 4: minimum executable check before full tournament/task evaluation."""

    def __init__(self, order: int, *, timeout_seconds: float = DEFAULT_STAGE_TIMEOUT_SECONDS) -> None:
        super().__init__(order)
        self._timeout_seconds = timeout_seconds

    @property
    def name(self) -> str:
        return "evaluation_ready"

    def run(self, candidate: Any, scenario: Any) -> StageResult:
        t0 = time.monotonic()

        # Dict strategies are always evaluation-ready if they got this far
        if isinstance(candidate, (dict, list)):
            return StageResult(
                stage=self.order, name=self.name, status=StageStatus.PASSED,
                duration_ms=_elapsed(t0),
            )

        # Code candidate — try to execute choose_action once
        if isinstance(candidate, str):
            try:
                fn = _load_choose_action(candidate, timeout_seconds=self._timeout_seconds)
            except TimeoutError:
                return StageResult(
                    stage=self.order, name=self.name, status=StageStatus.FAILED,
                    duration_ms=_elapsed(t0),
                    error="timed out while loading choose_action",
                    error_code="timeout",
                )
            except Exception as exc:
                return StageResult(
                    stage=self.order, name=self.name, status=StageStatus.FAILED,
                    duration_ms=_elapsed(t0),
                    error=f"failed to load code: {exc}",
                    error_code="load_error",
                )

            initial_state_fn = getattr(scenario, "initial_state", None) if scenario else None
            state = initial_state_fn() if callable(initial_state_fn) else {}

            try:
                result = _run_choose_action(fn, state, timeout_seconds=self._timeout_seconds)
                if not isinstance(result, dict):
                    return StageResult(
                        stage=self.order, name=self.name, status=StageStatus.FAILED,
                        duration_ms=_elapsed(t0),
                        error=f"choose_action must return dict, got {type(result).__name__}",
                        error_code="invalid_return_type",
                    )
            except TimeoutError:
                return StageResult(
                    stage=self.order, name=self.name, status=StageStatus.FAILED,
                    duration_ms=_elapsed(t0),
                    error="timed out while executing choose_action",
                    error_code="timeout",
                )
            except Exception as exc:
                return StageResult(
                    stage=self.order, name=self.name, status=StageStatus.FAILED,
                    duration_ms=_elapsed(t0),
                    error=f"choose_action raised: {exc}",
                    error_code="execution_error",
                )

        return StageResult(
            stage=self.order, name=self.name, status=StageStatus.PASSED,
            duration_ms=_elapsed(t0),
        )


# ── ValidationMetrics ────────────────────────────────────────────────────


class ValidationMetrics:
    """Tracks cumulative validation cost per generation.

    Counts candidates rejected at each stage and estimates expensive
    evaluations avoided by early rejection.
    """

    def __init__(self) -> None:
        self.total_candidates: int = 0
        self.total_rejected: int = 0
        self.rejections_by_stage: dict[str, int] = {}
        self._total_duration_ms: float = 0.0

    def record(self, results: list[StageResult]) -> None:
        """Record one validation run's results."""
        self.total_candidates += 1
        total_ms = sum(r.duration_ms for r in results)
        self._total_duration_ms += total_ms

        for r in results:
            if r.status is StageStatus.FAILED:
                self.total_rejected += 1
                self.rejections_by_stage[r.name] = self.rejections_by_stage.get(r.name, 0) + 1
                break  # Only count the first failure

    @property
    def estimated_evaluations_saved(self) -> int:
        """Number of expensive evaluations avoided by early rejection."""
        return self.total_rejected

    def to_event_payload(self) -> dict[str, Any]:
        """Format as event payload for dashboard emission."""
        return {
            "total_candidates": self.total_candidates,
            "total_rejected": self.total_rejected,
            "rejections_by_stage": dict(self.rejections_by_stage),
            "estimated_evaluations_saved": self.estimated_evaluations_saved,
            "total_validation_ms": round(self._total_duration_ms, 2),
        }

    def reset(self) -> None:
        """Reset all counters."""
        self.total_candidates = 0
        self.total_rejected = 0
        self.rejections_by_stage.clear()
        self._total_duration_ms = 0.0


# ── ValidationRunner ─────────────────────────────────────────────────────


class ValidationRunner:
    """Runs a validation pipeline and tracks metrics.

    Wraps ``ValidationPipeline`` with ``ValidationMetrics`` accumulation.
    Event emission is the caller's responsibility — the runner only
    provides ``metrics.to_event_payload()`` for emission.
    """

    def __init__(self, pipeline: ValidationPipeline) -> None:
        self._pipeline = pipeline
        self._metrics = ValidationMetrics()

    @property
    def metrics(self) -> ValidationMetrics:
        return self._metrics

    def validate(self, candidate: Any, scenario: Any) -> list[StageResult]:
        """Run the pipeline and record metrics. Returns stage results."""
        results = self._pipeline.run(candidate, scenario)
        self._metrics.record(results)
        return results

    def reset_metrics(self) -> None:
        """Reset accumulated metrics (e.g., at generation boundary)."""
        self._metrics.reset()


# ── Factory ──────────────────────────────────────────────────────────────


def default_pipeline() -> ValidationPipeline:
    """Create the standard 5-stage validation pipeline."""
    return ValidationPipeline(stages=[
        SyntaxStage(order=0),
        ContractStage(order=1),
        DeterministicStage(order=2),
        EdgeCaseStage(order=3),
        EvaluationReadyStage(order=4),
    ])


# ── Helpers ──────────────────────────────────────────────────────────────


def _elapsed(t0: float) -> float:
    return (time.monotonic() - t0) * 1000


def _load_choose_action(source: str, *, timeout_seconds: float = DEFAULT_STAGE_TIMEOUT_SECONDS) -> Any:
    """Load code and extract choose_action function."""
    from autocontext.execution.harness_loader import _SAFE_BUILTINS, _exec_harness_source, _HarnessTimeout, _run_with_timeout

    namespace: dict[str, Any] = {"__builtins__": dict(_SAFE_BUILTINS)}
    try:
        _run_with_timeout(
            lambda: _exec_harness_source(source, namespace),
            timeout_seconds,
        )
    except _HarnessTimeout as exc:
        raise TimeoutError("loading choose_action timed out") from exc

    fn = namespace.get("choose_action")
    if not callable(fn):
        raise ValueError("choose_action not found or not callable")
    return fn


def _run_choose_action(
    fn: Any,
    state: dict[str, Any],
    *,
    timeout_seconds: float = DEFAULT_STAGE_TIMEOUT_SECONDS,
) -> Any:
    """Run choose_action with the same timeout discipline as harness loading."""
    from autocontext.execution.harness_loader import _HarnessTimeout, _run_with_timeout

    try:
        return _run_with_timeout(lambda: fn(state), timeout_seconds)
    except _HarnessTimeout as exc:
        raise TimeoutError("choose_action timed out") from exc
