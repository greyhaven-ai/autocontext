"""PolicyExecutor — zero-LLM match execution of code policies.

Runs a Python code policy (``choose_action(state) -> dict``) against a
ScenarioInterface without any LLM calls. Policies are AST-safety-checked,
executed in a restricted-builtins sandbox with timeout enforcement.
"""
from __future__ import annotations

import collections as _collections
import logging
import math as _math
import re as _re
import signal
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, cast

from autocontext.execution.ast_safety import check_ast_safety
from autocontext.execution.harness_loader import _SAFE_BUILTINS
from autocontext.execution.isolated_python import (
    DEFAULT_MAX_MEMORY_MB,
    DEFAULT_MAX_OUTPUT_BYTES,
    IsolatedExecutionError,
    IsolatedExecutionTimeout,
    IsolationUnavailableError,
    run_isolated_json,
)
from autocontext.scenarios.base import ScenarioInterface

logger = logging.getLogger(__name__)

_MATH_FACADE_NAMES = (
    "acos", "acosh", "asin", "asinh", "atan", "atan2", "atanh",
    "ceil", "comb", "copysign", "cos", "cosh", "degrees", "dist",
    "e", "erf", "erfc", "exp", "exp2", "expm1", "fabs", "factorial",
    "floor", "fmod", "frexp", "fsum", "gamma", "gcd", "hypot", "inf",
    "isclose", "isfinite", "isinf", "isnan", "isqrt", "lcm", "ldexp",
    "lgamma", "log", "log10", "log1p", "log2", "modf", "nan", "nextafter",
    "perm", "pi", "pow", "prod", "radians", "remainder", "sin", "sinh",
    "sqrt", "tan", "tanh", "tau", "trunc", "ulp",
)
_COLLECTIONS_FACADE_NAMES = (
    "ChainMap",
    "Counter",
    "OrderedDict",
    "defaultdict",
    "deque",
    "namedtuple",
)
_RE_FACADE_NAMES = (
    "A", "ASCII", "DOTALL", "I", "IGNORECASE", "L", "LOCALE", "M",
    "MULTILINE", "NOFLAG", "S", "U", "UNICODE", "VERBOSE", "X", "compile",
    "escape", "findall", "finditer", "fullmatch", "match", "search", "split",
    "sub", "subn",
)


def _module_facade(module: Any, names: tuple[str, ...]) -> SimpleNamespace:
    """Copy an explicit capability allowlist without exposing the source module."""
    return SimpleNamespace(**{name: getattr(module, name) for name in names})


def _build_module_facades() -> dict[str, SimpleNamespace]:
    """Build fresh facades so one policy cannot poison a later execution."""
    return {
        "math": _module_facade(_math, _MATH_FACADE_NAMES),
        "collections": _module_facade(_collections, _COLLECTIONS_FACADE_NAMES),
        "re": _module_facade(_re, _RE_FACADE_NAMES),
    }


@dataclass(frozen=True, slots=True)
class PolicyMatchResult:
    """Result of executing a single policy match against a scenario."""

    score: float
    normalized_score: float
    had_illegal_actions: bool
    illegal_action_count: int
    errors: list[str]
    moves_played: int
    replay: dict[str, Any] | None


def _failed_policy_result(error: str) -> PolicyMatchResult:
    return PolicyMatchResult(
        score=0.0,
        normalized_score=0.0,
        had_illegal_actions=False,
        illegal_action_count=0,
        errors=[error],
        moves_played=0,
        replay=None,
    )


def _policy_result_to_wire(result: PolicyMatchResult) -> dict[str, Any]:
    """Convert a child result to the JSON-only isolation protocol shape."""
    return {
        "score": result.score,
        "normalized_score": result.normalized_score,
        "had_illegal_actions": result.had_illegal_actions,
        "illegal_action_count": result.illegal_action_count,
        "errors": result.errors,
        "moves_played": result.moves_played,
        "replay": result.replay,
    }


def _policy_result_from_wire(raw: Any) -> PolicyMatchResult:
    """Validate the untrusted child response before exposing it to callers."""
    if not isinstance(raw, Mapping):
        raise ValueError("policy child result must be an object")
    score = raw.get("score")
    normalized_score = raw.get("normalized_score")
    had_illegal_actions = raw.get("had_illegal_actions")
    illegal_action_count = raw.get("illegal_action_count")
    errors = raw.get("errors")
    moves_played = raw.get("moves_played")
    replay = raw.get("replay")
    if isinstance(score, bool) or not isinstance(score, (int, float)) or not _math.isfinite(score):
        raise ValueError("policy child returned an invalid score")
    if (
        isinstance(normalized_score, bool)
        or not isinstance(normalized_score, (int, float))
        or not _math.isfinite(normalized_score)
    ):
        raise ValueError("policy child returned an invalid normalized score")
    if not isinstance(had_illegal_actions, bool):
        raise ValueError("policy child returned an invalid legality flag")
    if (
        isinstance(illegal_action_count, bool)
        or not isinstance(illegal_action_count, int)
        or illegal_action_count < 0
    ):
        raise ValueError("policy child returned an invalid illegal-action count")
    if not isinstance(errors, list) or not all(isinstance(error, str) for error in errors):
        raise ValueError("policy child returned invalid errors")
    if isinstance(moves_played, bool) or not isinstance(moves_played, int) or moves_played < 0:
        raise ValueError("policy child returned an invalid move count")
    if replay is not None and not isinstance(replay, dict):
        raise ValueError("policy child returned an invalid replay")
    return PolicyMatchResult(
        score=float(score),
        normalized_score=float(normalized_score),
        had_illegal_actions=had_illegal_actions,
        illegal_action_count=illegal_action_count,
        errors=errors,
        moves_played=moves_played,
        replay=replay,
    )


class _PolicyTimeout(Exception):
    """Raised when policy execution exceeds the time limit."""


def _run_with_timeout(fn: Callable[[], Any], timeout_seconds: float) -> Any:
    """Run *fn* with a wall-clock timeout.

    Uses SIGALRM on the main thread (macOS/Linux) for reliable interruption.
    Worker threads fail closed because Python cannot safely terminate hostile
    code that has started running in another thread.
    """
    if threading.current_thread() is not threading.main_thread():
        raise _PolicyTimeout

    old_handler = signal.getsignal(signal.SIGALRM)

    def _alarm_handler(signum: int, frame: Any) -> None:
        raise _PolicyTimeout

    try:
        signal.signal(signal.SIGALRM, _alarm_handler)
        signal.setitimer(signal.ITIMER_REAL, timeout_seconds)
        return fn()
    except _PolicyTimeout:
        raise
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old_handler)


def _exec_policy_source(source: str, namespace: dict[str, Any]) -> None:
    """Run policy source code in a restricted namespace.

    Security note: This runs user-provided policy code in a namespace with
    restricted builtins. The code is AST-validated before execution.
    Only called on source that has passed ast.parse() and AST safety checks.
    """
    code = compile(source, "<policy>", "exec")  # noqa: S102
    exec(code, namespace)  # noqa: S102


class PolicyExecutor:
    """Executes Python code policies against a scenario with zero LLM calls.

    Policies must define ``choose_action(state) -> dict`` which is called
    with the scenario's game state and must return an action dict compatible
    with ``scenario.validate_actions()``.

    Security: policies are AST-safety-checked via ``check_ast_safety()``,
    run in a namespace with restricted builtins, and subject to per-match
    timeout enforcement.
    """

    def __init__(
        self,
        scenario: ScenarioInterface,
        *,
        timeout_per_match: float = 30.0,
        max_moves_per_match: int = 128,
        safe_builtins: bool = True,
        max_memory_mb: int = DEFAULT_MAX_MEMORY_MB,
        max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
    ) -> None:
        self._scenario = scenario
        self._timeout_per_match = timeout_per_match
        self._max_moves_per_match = max(1, max_moves_per_match)
        self._safe_builtins = safe_builtins
        self._max_memory_mb = max_memory_mb
        self._max_output_bytes = max_output_bytes

    def _build_namespace(self) -> dict[str, Any]:
        """Build the restricted namespace for policy execution."""
        ns: dict[str, Any] = {}
        if self._safe_builtins:
            ns["__builtins__"] = dict(_SAFE_BUILTINS)
        else:
            ns["__builtins__"] = __builtins__
        # Expose only fresh, explicit capability facades rather than the full
        # module objects (which contain paths back to sys.modules/builtins).
        ns.update(_build_module_facades())
        return ns

    def _compile_policy(self, policy_source: str) -> tuple[Callable[..., dict[str, Any]] | None, list[str]]:
        """Compile and load a policy, returning (choose_action_fn, errors).

        Returns (None, errors) if the policy fails safety or compilation checks.
        """
        # AST safety check
        violations = check_ast_safety(policy_source)
        if violations:
            return None, [f"AST safety violation: {v}" for v in violations]

        # Execute in restricted namespace to define the choose_action function
        namespace = self._build_namespace()
        try:
            _run_with_timeout(
                lambda: _exec_policy_source(policy_source, namespace),
                self._timeout_per_match,
            )
        except _PolicyTimeout:
            return None, [f"policy definition timed out ({self._timeout_per_match:.1f}s)"]
        except SyntaxError as exc:
            return None, [f"syntax error: {exc}"]
        except Exception as exc:
            logger.debug("execution.policy_executor: caught Exception", exc_info=True)
            return None, [f"policy definition error: {exc}"]

        choose_action = namespace.get("choose_action")
        if not callable(choose_action):
            return None, ["policy must define a callable 'choose_action(state) -> dict'"]

        return choose_action, []

    def _execute_single_match(
        self,
        choose_action: Callable[..., dict[str, Any]],
        seed: int | None,
    ) -> PolicyMatchResult:
        """Execute one match by repeatedly stepping until the scenario terminates."""
        scenario = self._scenario
        state = scenario.initial_state(seed=seed)
        moves_played = 0

        try:
            while not scenario.is_terminal(state):
                if moves_played >= self._max_moves_per_match:
                    return PolicyMatchResult(
                        score=0.0,
                        normalized_score=0.0,
                        had_illegal_actions=False,
                        illegal_action_count=0,
                        errors=[f"policy exceeded max moves ({self._max_moves_per_match})"],
                        moves_played=moves_played,
                        replay=None,
                    )

                try:
                    action = choose_action(state)
                except _PolicyTimeout:
                    raise
                except Exception as exc:
                    logger.debug("execution.policy_executor: caught Exception", exc_info=True)
                    return PolicyMatchResult(
                        score=0.0,
                        normalized_score=0.0,
                        had_illegal_actions=False,
                        illegal_action_count=0,
                        errors=[f"policy raised exception: {exc}"],
                        moves_played=moves_played,
                        replay=None,
                    )

                if not isinstance(action, dict):
                    return PolicyMatchResult(
                        score=0.0,
                        normalized_score=0.0,
                        had_illegal_actions=True,
                        illegal_action_count=1,
                        errors=[f"choose_action must return a dict, got {type(action).__name__}"],
                        moves_played=moves_played,
                        replay=None,
                    )

                valid, reason = scenario.validate_actions(state, "challenger", action)
                if not valid:
                    return PolicyMatchResult(
                        score=0.0,
                        normalized_score=0.0,
                        had_illegal_actions=True,
                        illegal_action_count=1,
                        errors=[],
                        moves_played=moves_played,
                        replay=None,
                    )

                state = scenario.step(state, action)
                moves_played += 1
        except _PolicyTimeout:
            raise
        except Exception as exc:
            logger.debug("execution.policy_executor: caught Exception", exc_info=True)
            return PolicyMatchResult(
                score=0.0,
                normalized_score=0.0,
                had_illegal_actions=False,
                illegal_action_count=0,
                errors=[f"scenario execution failed: {exc}"],
                moves_played=moves_played,
                replay=None,
            )

        result = scenario.get_result(state)
        score = result.score
        # Normalize score to [0, 1] — scores from scenarios are already in [0, 1]
        normalized_score = max(0.0, min(1.0, score))

        replay = {
            "score": score,
            "replay": result.replay,
            "metrics": result.metrics,
            "summary": result.summary,
        }

        return PolicyMatchResult(
            score=score,
            normalized_score=normalized_score,
            had_illegal_actions=False,
            illegal_action_count=0,
            errors=[],
            moves_played=moves_played,
            replay=replay,
        )

    def _execute_match_in_child(self, policy_source: str, seed: int | None) -> PolicyMatchResult:
        """Compile and execute one match inside the already-isolated child."""
        choose_action, compile_errors = self._compile_policy(policy_source)
        if choose_action is None:
            return PolicyMatchResult(
                score=0.0,
                normalized_score=0.0,
                had_illegal_actions=False,
                illegal_action_count=0,
                errors=compile_errors,
                moves_played=0,
                replay=None,
            )

        try:
            result = _run_with_timeout(
                lambda: self._execute_single_match(choose_action, seed),
                self._timeout_per_match,
            )
            return cast(PolicyMatchResult, result)
        except _PolicyTimeout:
            return _failed_policy_result(
                f"policy execution timed out ({self._timeout_per_match:.1f}s)"
            )

    def execute_match(self, policy_source: str, seed: int | None = None) -> PolicyMatchResult:
        """Execute a single match in a killable, resource-bounded child.

        Args:
            policy_source: Python source code defining ``choose_action(state) -> dict``.
            seed: Optional seed for deterministic scenario initialization.

        Returns:
            PolicyMatchResult with score, legality info, and errors.
        """
        violations = check_ast_safety(policy_source)
        if violations:
            return PolicyMatchResult(
                score=0.0,
                normalized_score=0.0,
                had_illegal_actions=False,
                illegal_action_count=0,
                errors=[f"AST safety violation: {violation}" for violation in violations],
                moves_played=0,
                replay=None,
            )
        try:
            raw = run_isolated_json(
                lambda: _policy_result_to_wire(
                    self._execute_match_in_child(policy_source, seed)
                ),
                timeout_seconds=self._timeout_per_match,
                max_memory_mb=self._max_memory_mb,
                max_output_bytes=self._max_output_bytes,
            )
        except IsolatedExecutionTimeout:
            return _failed_policy_result(
                f"policy execution timed out ({self._timeout_per_match:.1f}s)"
            )
        except IsolationUnavailableError as exc:
            return _failed_policy_result(f"policy isolation unavailable: {exc}")
        except IsolatedExecutionError as exc:
            return _failed_policy_result(f"policy isolated execution failed: {exc}")

        try:
            return _policy_result_from_wire(raw)
        except ValueError as exc:
            return _failed_policy_result(f"policy isolated execution failed: {exc}")

    def execute_batch(
        self,
        policy_source: str,
        n_matches: int = 5,
        seeds: list[int] | None = None,
    ) -> list[PolicyMatchResult]:
        """Execute multiple matches of a policy against the scenario.

        Args:
            policy_source: Python source code defining ``choose_action(state) -> dict``.
            n_matches: Number of matches to run (default 5). Ignored if seeds is provided.
            seeds: Optional explicit list of seeds; length determines actual match count.

        Returns:
            List of PolicyMatchResult, one per match.
        """
        if seeds is not None:
            effective_seeds = seeds
        else:
            effective_seeds = list(range(n_matches))

        return [self.execute_match(policy_source, seed=s) for s in effective_seeds]
