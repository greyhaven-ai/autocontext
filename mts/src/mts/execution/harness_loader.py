"""HarnessLoader — loads and runs architect-generated executable validators.

Loads .py files from knowledge/<scenario>/harness/, AST-validates them,
and extracts validate_strategy / enumerate_legal_actions / parse_game_state
callables from each file's namespace.
"""
from __future__ import annotations

import ast
import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger(__name__)

_SAFE_BUILTINS = {
    k: __builtins__[k] if isinstance(__builtins__, dict) else getattr(__builtins__, k)  # type: ignore[index]
    for k in (
        "abs", "all", "any", "bool", "dict", "enumerate", "filter", "float",
        "frozenset", "int", "isinstance", "issubclass", "len", "list", "map",
        "max", "min", "print", "range", "repr", "reversed", "round", "set",
        "sorted", "str", "sum", "tuple", "type", "zip",
    )
}


@dataclass(slots=True, frozen=True)
class HarnessValidationResult:
    """Result of running harness validators against a strategy."""

    passed: bool
    errors: list[str]
    validator_name: str = ""


def _exec_harness_source(source: str, namespace: dict[str, Any]) -> None:
    """Execute harness source code in a restricted namespace.

    Security note: This runs architect-generated code in a namespace with
    restricted builtins. The code is AST-validated before execution.
    Only called on files that have passed ast.parse() validation.
    """
    exec(source, namespace)  # noqa: S102


class HarnessLoader:
    """Loads harness validator .py files and runs their validate_strategy functions."""

    def __init__(self, harness_dir: Path) -> None:
        self._harness_dir = harness_dir
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
                LOGGER.warning("skipping harness '%s': syntax error", name)
                continue

            # Execute in restricted namespace
            namespace: dict[str, Any] = {"__builtins__": dict(_SAFE_BUILTINS)}
            try:
                _exec_harness_source(source, namespace)
            except Exception:
                LOGGER.warning("skipping harness '%s': execution error", name, exc_info=True)
                continue

            # Extract known callables
            file_callables: dict[str, Callable[..., Any]] = {}
            for fn_name in ("validate_strategy", "enumerate_legal_actions", "parse_game_state"):
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
                passed, errors = validator_fn(strategy, scenario)
                if not passed:
                    all_errors.extend(f"[{name}] {e}" for e in errors)
            except Exception as exc:
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
