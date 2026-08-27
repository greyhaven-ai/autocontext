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
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from autocontext.execution.ast_safety import check_ast_safety
from autocontext.execution.isolated_python import (
    DEFAULT_MAX_MEMORY_MB,
    DEFAULT_MAX_OUTPUT_BYTES,
    IsolatedExecutionError,
    IsolatedExecutionTimeout,
    IsolationUnavailableError,
    run_isolated_json,
)
from autocontext.security.confined_files import list_confined_regular_files, read_confined_text
from autocontext.storage.artifact_harness_codegen import (
    MAX_HARNESS_CONTEXT_BYTES,
    MAX_HARNESS_DIRECTORY_ENTRIES,
    MAX_HARNESS_SOURCE_BYTES,
)

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

_KNOWN_CALLABLES = (
    "validate_strategy",
    "enumerate_legal_actions",
    "parse_game_state",
    "is_legal_action",
)


class _HarnessTimeout(Exception):
    """Raised when harness execution exceeds the time limit."""


def _run_with_timeout(fn: Callable[[], Any], timeout_seconds: float) -> Any:
    """Run *fn* with a wall-clock timeout.

    Uses SIGALRM on the main thread (macOS/Linux) for reliable interruption.
    Python cannot safely terminate hostile code in a worker thread, so worker
    threads fail closed instead of starting execution that could outlive its timeout.
    """
    if threading.current_thread() is not threading.main_thread():
        raise _HarnessTimeout

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


def _inspect_harness_source(source: str) -> list[str]:
    """Execute source in the isolated child and report its public callables."""
    namespace: dict[str, Any] = {"__builtins__": dict(_SAFE_BUILTINS)}
    _exec_harness_source(source, namespace)
    return [name for name in _KNOWN_CALLABLES if callable(namespace.get(name))]


def _call_harness_source(
    source: str,
    fn_name: str,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> Any:
    """Load and invoke one harness function inside the isolated child."""
    namespace: dict[str, Any] = {"__builtins__": dict(_SAFE_BUILTINS)}
    _exec_harness_source(source, namespace)
    fn = namespace.get(fn_name)
    if not callable(fn):
        raise ValueError(f"harness callable '{fn_name}' is unavailable")
    return fn(*args, **kwargs)


@dataclass(slots=True, frozen=True)
class _HarnessCallableProxy:
    """Callable facade that starts a fresh isolated child for each invocation."""

    source: str
    fn_name: str
    timeout_seconds: float
    max_memory_mb: int
    max_output_bytes: int

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        return run_isolated_json(
            lambda: _call_harness_source(self.source, self.fn_name, args, kwargs),
            timeout_seconds=self.timeout_seconds,
            max_memory_mb=self.max_memory_mb,
            max_output_bytes=self.max_output_bytes,
        )


class HarnessLoader:
    """Loads harness metadata and runs validators in killable child processes."""

    def __init__(
        self,
        harness_dir: Path,
        *,
        timeout_seconds: float = 5.0,
        max_memory_mb: int = DEFAULT_MAX_MEMORY_MB,
        max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
    ) -> None:
        self._harness_dir = harness_dir
        self._timeout_seconds = timeout_seconds
        self._max_memory_mb = max_memory_mb
        self._max_output_bytes = max_output_bytes
        self._validators: dict[str, Callable[..., tuple[bool, list[str]]]] = {}
        self._callables: dict[str, dict[str, Callable[..., Any]]] = {}
        self._load_errors: list[str] = []

    def _confined_location(self) -> tuple[Path, tuple[str, ...]]:
        """Anchor the two mutable path components above a harness directory."""
        directory = self._harness_dir.absolute()
        if not directory.name:
            raise OSError("harness directory must have a name")
        parent = directory.parent
        if parent.name:
            return parent.parent, (parent.name, directory.name)
        return parent, (directory.name,)

    def load(self) -> list[str]:
        """Load all .py files from the harness directory. Returns list of loaded names."""
        self._validators.clear()
        self._callables.clear()
        self._load_errors.clear()
        loaded: list[str] = []
        try:
            root, parts = self._confined_location()
            filenames = list_confined_regular_files(
                root,
                parts,
                suffix=".py",
                max_entries=MAX_HARNESS_DIRECTORY_ENTRIES,
            )
        except FileNotFoundError:
            return loaded
        except OSError:
            logger.warning("unable to access confined harness directory", exc_info=True)
            self._load_errors.append("[harness] harness directory could not be read safely")
            return loaded

        loaded_source_bytes = 0
        for filename in filenames:
            name = Path(filename).stem
            try:
                source = read_confined_text(
                    root,
                    parts,
                    filename,
                    max_bytes=MAX_HARNESS_SOURCE_BYTES,
                )
            except OSError:
                logger.warning("skipping harness '%s': unable to read source", name, exc_info=True)
                self._load_errors.append(f"[{name}] harness source could not be read")
                continue
            if source is None:
                continue
            loaded_source_bytes += len(source.encode("utf-8"))
            if loaded_source_bytes > MAX_HARNESS_CONTEXT_BYTES:
                logger.warning("stopping harness load: aggregate source exceeds byte limit")
                self._load_errors.append("[harness] aggregate harness source exceeds byte limit")
                break

            # AST-validate before executing
            try:
                ast.parse(source)
            except SyntaxError:
                logger.warning("skipping harness '%s': syntax error", name)
                self._load_errors.append(f"[{name}] harness has a syntax error")
                continue

            # AST safety check — reject dangerous patterns
            violations = check_ast_safety(source)
            if violations:
                logger.warning(
                    "skipping harness '%s': AST safety violations: %s",
                    name, "; ".join(violations),
                )
                self._load_errors.append(
                    f"[{name}] harness failed AST safety validation: {'; '.join(violations)}"
                )
                continue

            # Execute top-level definitions only in a killable child.  The
            # parent stores isolated proxies, never child-created functions.
            try:
                def _inspect_source(src: str = source) -> list[str]:
                    return _inspect_harness_source(src)

                callable_names = run_isolated_json(
                    _inspect_source,
                    timeout_seconds=self._timeout_seconds,
                    max_memory_mb=self._max_memory_mb,
                    max_output_bytes=self._max_output_bytes,
                )
            except IsolatedExecutionTimeout:
                logger.warning("skipping harness '%s': timed out (%.1fs)", name, self._timeout_seconds)
                self._load_errors.append(
                    f"[{name}] harness load timed out ({self._timeout_seconds:.1f}s)"
                )
                continue
            except IsolationUnavailableError as exc:
                logger.warning("skipping harness '%s': isolation unavailable", name)
                self._load_errors.append(f"[{name}] harness isolation unavailable: {exc}")
                continue
            except IsolatedExecutionError:
                logger.warning("skipping harness '%s': execution error", name, exc_info=True)
                self._load_errors.append(f"[{name}] harness raised an error while loading")
                continue
            if (
                not isinstance(callable_names, list)
                or not all(callable_name in _KNOWN_CALLABLES for callable_name in callable_names)
            ):
                logger.warning("skipping harness '%s': invalid isolated metadata", name)
                self._load_errors.append(f"[{name}] harness returned invalid callable metadata")
                continue

            # Extract known callables
            file_callables: dict[str, Callable[..., Any]] = {}
            for fn_name in callable_names:
                file_callables[fn_name] = _HarnessCallableProxy(
                    source=source,
                    fn_name=fn_name,
                    timeout_seconds=self._timeout_seconds,
                    max_memory_mb=self._max_memory_mb,
                    max_output_bytes=self._max_output_bytes,
                )

            if "validate_strategy" in file_callables:
                self._validators[name] = file_callables["validate_strategy"]
            self._callables[name] = file_callables
            loaded.append(name)

        return loaded

    def validate_strategy(self, strategy: dict[str, Any], scenario: Any) -> HarnessValidationResult:
        """Run all loaded validators against a strategy. Returns aggregate result."""
        all_errors = list(self._load_errors)
        for name, validator_fn in self._validators.items():
            try:
                result = validator_fn(strategy, scenario)
                if (
                    not isinstance(result, (list, tuple))
                    or len(result) != 2
                    or not isinstance(result[0], bool)
                    or not isinstance(result[1], list)
                    or not all(isinstance(error, str) for error in result[1])
                ):
                    raise ValueError("validator must return (bool, list[str])")
                passed, errors = result
                if not passed:
                    all_errors.extend(f"[{name}] {e}" for e in errors)
            except IsolatedExecutionTimeout:
                all_errors.append(f"[{name}] validator timed out ({self._timeout_seconds:.1f}s)")
            except IsolationUnavailableError as exc:
                all_errors.append(f"[{name}] validator isolation unavailable: {exc}")
            except IsolatedExecutionError as exc:
                all_errors.append(f"[{name}] validator raised exception: {exc}")
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

    @property
    def load_errors(self) -> list[str]:
        """Return failures that prevented configured harness files from loading."""
        return list(self._load_errors)
