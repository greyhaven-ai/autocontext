"""HarnessLoader — loads and runs architect-generated executable validators.

Loads .py files from knowledge/<scenario>/harness/, AST-validates them,
and extracts validate_strategy / enumerate_legal_actions / parse_game_state
callables from each file's namespace.
"""
from __future__ import annotations

import ast
import logging
import signal
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from autocontext.execution.ast_safety import check_ast_safety

logger = logging.getLogger(__name__)

_SAFE_BUILTINS = {
    k: __builtins__[k] if isinstance(__builtins__, dict) else getattr(__builtins__, k)
    for k in (
        "abs", "all", "any", "bool", "dict", "enumerate", "filter", "float",
        "frozenset", "int", "isinstance", "issubclass", "len", "list", "map",
        "max", "min", "print", "range", "repr", "reversed", "round", "set",
        "sorted", "str", "sum", "tuple", "zip",
    )
}


class _HarnessTimeout(Exception):
    """Raised when harness execution exceeds the time limit."""


def _run_with_timeout(fn: Callable[[], Any], timeout_seconds: float) -> Any:
    """Run *fn* with a wall-clock timeout.

    Uses SIGALRM on the main thread (macOS/Linux) for reliable interruption,
    falls back to ThreadPoolExecutor on worker threads.
    """
    if threading.current_thread() is threading.main_thread():
        old_handler = signal.getsignal(signal.SIGALRM)
        def _alarm_handler(signum: int, frame: Any) -> None:
            raise _HarnessTimeout
        try:
            signal.signal(signal.SIGALRM, _alarm_handler)
            signal.setitimer(signal.ITIMER_REAL, timeout_seconds)
            return fn()
        except _HarnessTimeout:
            raise
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, old_handler)
    else:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(fn)
            try:
                return future.result(timeout=timeout_seconds)
            except FuturesTimeoutError:
                raise _HarnessTimeout from None


@dataclass(slots=True, frozen=True)
class HarnessValidationResult:
    """Result of running harness validators against a strategy."""

    passed: bool
    errors: list[str]
    validator_name: str = ""


def _exec_harness_source(source: str, namespace: dict[str, Any]) -> None:
    """Run harness source code in a restricted namespace.

    Security note: This runs architect-generated code in a namespace with
    restricted builtins. The code is AST-validated before execution.
    Only called on files that have passed ast.parse() and AST safety checks.
    """
    # Security: exec is intentional here — code has been AST-safety-checked
    # and runs in a restricted-builtins namespace.
    code = compile(source, "<harness>", "exec")  # noqa: S102
    exec(code, namespace)  # noqa: S102


class HarnessLoader:
    """Loads harness validator .py files and runs their validate_strategy functions."""

    def __init__(self, harness_dir: Path, *, timeout_seconds: float = 5.0) -> None:
        self._harness_dir = harness_dir
        self._timeout_seconds = timeout_seconds
        self._validators: dict[str, Callable[..., tuple[bool, list[str]]]] = {}
        self._callables: dict[str, dict[str, Callable[..., Any]]] = {}

    def load(self) -> list[str]:
        """Load all .py files from the harness directory. Returns list of loaded names."""
        loaded: list[str] = []
        if not self._harness_dir.exists():
            return loaded

        for py_file in sorted(self._harness_dir.glob("*.py")):
            name = py_file.stem
            source = py_file.read_text(encoding="utf-8")

            # AST-validate before executing
            try:
                ast.parse(source)
            except SyntaxError:
                logger.warning("skipping harness '%s': syntax error", name)
                continue

            # AST safety check — reject dangerous patterns
            violations = check_ast_safety(source)
            if violations:
                logger.warning(
                    "skipping harness '%s': AST safety violations: %s",
                    name, "; ".join(violations),
                )
                continue

            # Run in restricted namespace with timeout
            namespace: dict[str, Any] = {"__builtins__": dict(_SAFE_BUILTINS)}
            try:
                def _run_exec(ns: dict[str, Any] = namespace, src: str = source) -> None:
                    _exec_harness_source(src, ns)

                _run_with_timeout(_run_exec, self._timeout_seconds)
            except _HarnessTimeout:
                logger.warning("skipping harness '%s': timed out (%.1fs)", name, self._timeout_seconds)
                continue
            except Exception:
                logger.warning("skipping harness '%s': execution error", name, exc_info=True)
                continue

            # Extract known callables
            file_callables: dict[str, Callable[..., Any]] = {}
            for fn_name in (
                "validate_strategy",
                "enumerate_legal_actions",
                "parse_game_state",
                "is_legal_action",
            ):
                fn = namespace.get(fn_name)
                if callable(fn):
                    file_callables[fn_name] = fn

            if "validate_strategy" in file_callables:
                self._validators[name] = file_callables["validate_strategy"]
            self._callables[name] = file_callables
            loaded.append(name)

        return loaded

    def validate_strategy(self, strategy: dict[str, Any], scenario: Any) -> HarnessValidationResult:
        """Run all loaded validators against a strategy. Returns aggregate result."""
        if not self._validators:
            return HarnessValidationResult(passed=True, errors=[])

        all_errors: list[str] = []
        for name, validator_fn in self._validators.items():
            try:
                def _run_validator(fn: Callable[..., Any] = validator_fn) -> tuple[bool, list[str]]:
                    result: tuple[bool, list[str]] = fn(strategy, scenario)
                    return result

                passed, errors = _run_with_timeout(_run_validator, self._timeout_seconds)
                if not passed:
                    all_errors.extend(f"[{name}] {e}" for e in errors)
            except _HarnessTimeout:
                all_errors.append(f"[{name}] validator timed out ({self._timeout_seconds:.1f}s)")
            except Exception as exc:
                logger.debug("execution.harness_loader: caught Exception", exc_info=True)
                all_errors.append(f"[{name}] validator raised exception: {exc}")

        return HarnessValidationResult(
            passed=len(all_errors) == 0,
            errors=all_errors,
        )

    def get_callable(self, file_name: str, fn_name: str) -> Callable[..., Any] | None:
        """Get a specific callable from a loaded harness file."""
        file_callables = self._callables.get(file_name, {})
        return file_callables.get(fn_name)

    def has_callable(self, file_name: str, fn_name: str) -> bool:
        """Check if a callable exists in a loaded harness file."""
        return self.get_callable(file_name, fn_name) is not None

    @property
    def loaded_names(self) -> list[str]:
        """Return names of all loaded harness files."""
        return list(self._callables.keys())
