"""Monty-backed REPL worker for sandboxed multi-turn exploration sessions."""
from __future__ import annotations

import ast
import json
import math
import re
import statistics
import time as time_mod
from collections.abc import Callable
from typing import Any

from autocontext.harness.repl.types import ReplCommand, ReplResult
from autocontext.harness.repl.worker import _chunk_by_headers, _chunk_by_size, _grep, _peek


def _create_repl_monty(code: str, inputs: list[str], external_functions: list[str]) -> Any:
    """Create a Monty interpreter instance. Separated for testability (mock target)."""
    try:
        import pydantic_monty
    except ImportError as exc:
        raise ImportError(
            "pydantic-monty is required for rlm_backend=monty. "
            "Install with: uv sync --extra monty"
        ) from exc
    return pydantic_monty.Monty(
        code,
        inputs=inputs,
        external_functions=external_functions,
    )


# ---------------------------------------------------------------------------
# Stdlib dispatch
# ---------------------------------------------------------------------------

_STDLIB_WHITELIST: dict[str, Any] = {
    "json": json,
    "math": math,
    "statistics": statistics,
    "re": re,
    "time": time_mod,
}

_STDLIB_FUNCTION_WHITELIST: dict[str, set[str]] = {
    "json": {"loads", "dumps"},
    "math": {"sqrt", "ceil", "floor", "log", "log10", "exp", "pow", "fabs", "isnan", "isinf"},
    "statistics": {"mean", "median", "stdev", "variance", "mode"},
    "re": {"findall", "search", "match", "sub", "split"},
    "time": {"time", "monotonic"},
}


def _stdlib_dispatch(module_name: str, func_name: str, *args: Any) -> Any:
    """Dispatch a stdlib call to a whitelisted module function."""
    if module_name not in _STDLIB_WHITELIST:
        raise ValueError(f"Module '{module_name}' not in stdlib whitelist: {sorted(_STDLIB_WHITELIST)}")
    allowed = _STDLIB_FUNCTION_WHITELIST.get(module_name, set())
    if func_name not in allowed:
        raise ValueError(f"Function '{func_name}' not allowed for module '{module_name}': {sorted(allowed)}")
    mod = _STDLIB_WHITELIST[module_name]
    fn = getattr(mod, func_name)
    return fn(*args)


# ---------------------------------------------------------------------------
# Text helper dispatch
# ---------------------------------------------------------------------------

_TEXT_HELPER_DISPATCH: dict[str, Callable[..., Any]] = {
    "peek": _peek,
    "grep": _grep,
    "chunk_by_size": _chunk_by_size,
    "chunk_by_headers": _chunk_by_headers,
}

# ---------------------------------------------------------------------------
# Trailing expression rewriting
# ---------------------------------------------------------------------------


def _rewrite_trailing_expr(code: str) -> str:
    """If the last statement is a bare expression (not a print call), rewrite to _print(repr(...))."""
    try:
        tree = ast.parse(code, mode="exec")
    except SyntaxError:
        return code

    if not tree.body:
        return code

    last = tree.body[-1]
    if not isinstance(last, ast.Expr):
        return code

    # Don't wrap if already a print/_print call
    if isinstance(last.value, ast.Call):
        func = last.value.func
        if isinstance(func, ast.Name) and func.id in ("print", "_print"):
            return code

    # Get the source text of the trailing expression
    lines = code.splitlines()
    # Use ast line info to locate the trailing expression
    expr_start = last.lineno - 1  # 0-indexed
    expr_end = last.end_lineno  # 1-indexed, so this is exclusive

    before = lines[:expr_start]
    expr_lines = lines[expr_start:expr_end]
    expr_text = "\n".join(expr_lines)

    # Strip any trailing whitespace/newlines from expr_text
    expr_text = expr_text.rstrip()

    before.append(f"_print(repr({expr_text}))")
    return "\n".join(before)


# ---------------------------------------------------------------------------
# Script template
# ---------------------------------------------------------------------------

_REPL_SCRIPT_TEMPLATE = """\
{user_code}

# Return persistent state for next turn
{{"answer": answer, "state": state}}
"""

_BASE_EXTERNAL_FUNCTIONS = [
    "_print",
    "stdlib",
    "peek",
    "grep",
    "chunk_by_size",
    "chunk_by_headers",
]


# ---------------------------------------------------------------------------
# MontyReplWorker
# ---------------------------------------------------------------------------


class MontyReplWorker:
    """Monty-backed REPL worker for sandboxed multi-turn exploration.

    Each ``run_code()`` call creates a fresh Monty interpreter. Cross-turn state
    persists via ``answer`` and ``state`` dicts passed as Monty inputs and extracted
    from outputs. Callables are exposed as Monty external functions dispatched on
    the host side.
    """

    def __init__(
        self,
        namespace: dict[str, Any] | None = None,
        max_stdout_chars: int = 8192,
        timeout_seconds: float = 10.0,
        max_external_calls: int = 500,
    ) -> None:
        self._max_stdout = max_stdout_chars
        self._timeout = timeout_seconds
        self._max_external_calls = max_external_calls

        self._namespace: dict[str, Any] = {
            "answer": {"content": "", "ready": False},
            "state": {},
        }
        if namespace:
            self._namespace.update(namespace)

    @property
    def namespace(self) -> dict[str, Any]:
        return self._namespace

    def _separate_namespace(self) -> tuple[dict[str, Any], dict[str, Callable[..., Any]]]:
        """Split namespace into JSON-serializable data and callables."""
        data: dict[str, Any] = {}
        callables: dict[str, Callable[..., Any]] = {}
        for key, value in self._namespace.items():
            if callable(value):
                callables[key] = value
            else:
                data[key] = value
        return data, callables

    def _build_dispatch(
        self,
        callables: dict[str, Callable[..., Any]],
        stdout_lines: list[str],
    ) -> Callable[[str, tuple[Any, ...]], Any]:
        """Build a dispatch function for Monty external function calls."""

        def dispatch(function_name: str, args: tuple[Any, ...]) -> Any:
            if function_name == "_print":
                text = str(args[0]) if args else ""
                stdout_lines.append(text)
                return None
            if function_name == "stdlib":
                return _stdlib_dispatch(*args)
            if function_name in _TEXT_HELPER_DISPATCH:
                return _TEXT_HELPER_DISPATCH[function_name](*args)
            if function_name in callables:
                return callables[function_name](*args)
            raise ValueError(f"Unknown external function: {function_name}")

        return dispatch

    def run_code(self, command: ReplCommand) -> ReplResult:
        """Run code in a fresh Monty sandbox and return the result."""
        # 1. Syntax check
        try:
            ast.parse(command.code, mode="exec")
        except SyntaxError as exc:
            return ReplResult(
                stdout="",
                error=f"SyntaxError: {exc}",
                answer=dict(self._namespace.get("answer", {"content": "", "ready": False})),
            )

        # 2. Rewrite trailing expression
        code = _rewrite_trailing_expr(command.code)

        # 3. Rewrite print() to _print()
        code = code.replace("print(", "_print(")

        # 4. Wrap in script template
        script = _REPL_SCRIPT_TEMPLATE.format(user_code=code)

        # 5. Separate namespace
        data, callables = self._separate_namespace()

        # 6. Build external function list
        ext_fns = list(_BASE_EXTERNAL_FUNCTIONS)
        for name in callables:
            if name not in ext_fns:
                ext_fns.append(name)

        input_names = sorted(data.keys())

        # 7. Create Monty interpreter
        try:
            monty = _create_repl_monty(
                code=script,
                inputs=input_names,
                external_functions=ext_fns,
            )
        except ImportError as exc:
            return ReplResult(
                stdout="",
                error=str(exc),
                answer=dict(self._namespace.get("answer", {"content": "", "ready": False})),
            )

        # 8. Dispatch loop
        stdout_lines: list[str] = []
        dispatch = self._build_dispatch(callables, stdout_lines)

        try:
            start_time = time_mod.monotonic()
            progress = monty.start(inputs=data)
            calls = 0

            while hasattr(progress, "function_name"):
                elapsed = time_mod.monotonic() - start_time
                if elapsed > self._timeout:
                    return ReplResult(
                        stdout="\n".join(stdout_lines),
                        error=f"Timeout: code exceeded {self._timeout}s",
                        answer=dict(self._namespace.get("answer", {"content": "", "ready": False})),
                    )
                calls += 1
                if calls > self._max_external_calls:
                    return ReplResult(
                        stdout="\n".join(stdout_lines),
                        error=f"Exceeded {self._max_external_calls} external function calls",
                        answer=dict(self._namespace.get("answer", {"content": "", "ready": False})),
                    )
                return_value = dispatch(progress.function_name, progress.args)
                progress = progress.resume(return_value=return_value)

        except Exception as exc:
            return ReplResult(
                stdout="\n".join(stdout_lines),
                error=str(exc),
                answer=dict(self._namespace.get("answer", {"content": "", "ready": False})),
            )

        # 9. Extract output and update namespace
        raw_output = progress.output
        if isinstance(raw_output, dict):
            if "answer" in raw_output:
                self._namespace["answer"] = raw_output["answer"]
            if "state" in raw_output:
                self._namespace["state"] = raw_output["state"]

        # 10. Build stdout with truncation
        stdout = "\n".join(stdout_lines)
        if len(stdout) > self._max_stdout:
            stdout = stdout[: self._max_stdout] + f"\n... [truncated at {self._max_stdout} chars]"

        # 11. Return result
        answer = dict(self._namespace.get("answer", {"content": "", "ready": False}))
        return ReplResult(stdout=stdout, error=None, answer=answer)
