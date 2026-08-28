"""Domain-agnostic REPL worker with sandboxed execution."""

from __future__ import annotations

import ast
import collections as _collections
import contextlib
import io
import json as _json
import logging
import math
import re as _re
import statistics as _statistics
import time as _time
from dataclasses import dataclass
from types import MappingProxyType
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
from autocontext.harness.repl.types import ReplCommand, ReplResult

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
_STATISTICS_FACADE_NAMES = (
    "StatisticsError", "correlation", "covariance", "fmean", "geometric_mean",
    "harmonic_mean", "linear_regression", "mean", "median", "median_grouped",
    "median_high", "median_low", "mode", "multimode", "pstdev", "pvariance",
    "quantiles", "stdev", "variance",
)
_COLLECTIONS_FACADE_NAMES = (
    "ChainMap", "Counter", "OrderedDict", "defaultdict", "deque", "namedtuple",
)
_RE_FACADE_NAMES = (
    "A", "ASCII", "DOTALL", "I", "IGNORECASE", "M", "MULTILINE", "NOFLAG",
    "S", "U", "UNICODE", "VERBOSE", "X", "compile", "escape", "findall",
    "finditer", "fullmatch", "match", "search", "split", "sub", "subn",
)
_TIME_FACADE_NAMES = (
    "asctime", "ctime", "gmtime", "localtime", "monotonic", "perf_counter",
    "process_time", "sleep", "strftime", "strptime", "time",
)
_SAFE_MODULE_NAMES = frozenset({"json", "math", "statistics", "collections", "re", "time"})

_SAFE_BUILTIN_NAMES = (
    "ArithmeticError", "AssertionError", "BaseException", "Exception",
    "IndexError", "KeyError", "LookupError", "RuntimeError", "StopIteration",
    "SystemExit", "TypeError", "ValueError", "ZeroDivisionError",
    "__build_class__", "abs", "all", "any", "bin", "bool", "bytearray",
    "bytes", "callable", "chr", "complex", "dict", "divmod", "enumerate",
    "filter", "float", "format", "frozenset", "hash", "hex", "int",
    "isinstance", "issubclass", "iter", "len", "list", "map", "max",
    "min", "next", "object", "oct", "ord", "pow", "print", "range",
    "repr", "reversed", "round", "set", "slice", "sorted", "str", "sum",
    "tuple", "zip",
)


def _peek(text: str, start: int = 0, length: int = 2000) -> str:
    """Return a slice of text starting at *start* for *length* chars."""
    return text[start : start + length]


def _grep(text: str, pattern: str, *, context: int = 0) -> list[str]:
    """Return lines matching *pattern* (case-insensitive). *context*=N includes surrounding lines."""
    import re as _re

    lines = text.splitlines()
    pat = _re.compile(_re.escape(pattern), _re.IGNORECASE)
    hits: list[str] = []
    for idx, line in enumerate(lines):
        if pat.search(line):
            lo = max(0, idx - context)
            hi = min(len(lines), idx + context + 1)
            hits.append("\n".join(lines[lo:hi]))
    return hits


def _chunk_by_size(text: str, size: int = 4000, overlap: int = 0) -> list[str]:
    """Split text into fixed-size chunks with optional overlap."""
    if not text:
        return []
    if size <= 0:
        raise ValueError("size must be positive")
    if overlap < 0 or overlap >= size:
        raise ValueError("overlap must be non-negative and less than size")
    chunks: list[str] = []
    step = size - overlap
    for start in range(0, len(text), step):
        chunk = text[start : start + size]
        if chunk:
            chunks.append(chunk)
        if start + size >= len(text):
            break
    return chunks


def _chunk_by_headers(text: str, pattern: str = r"^#{1,3} ") -> list[dict[str, str]]:
    """Split text at markdown header boundaries. Returns list of {header, content}."""
    import re as _re

    if not text:
        return []
    compiled = _re.compile(pattern, _re.MULTILINE)
    matches = list(compiled.finditer(text))
    if not matches:
        return [{"header": "", "content": text.strip()}]
    parts: list[dict[str, str]] = []
    if matches[0].start() > 0:
        preamble = text[: matches[0].start()].strip()
        if preamble:
            parts.append({"header": "", "content": preamble})
    for i, match in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        section = text[match.start() : end]
        nl = section.find("\n")
        if nl == -1:
            header, content = section.strip(), ""
        else:
            header, content = section[:nl].strip(), section[nl + 1 :].strip()
        parts.append({"header": header, "content": content})
    return parts


_TEXT_HELPERS: dict[str, Any] = {
    "peek": _peek,
    "grep": _grep,
    "chunk_by_size": _chunk_by_size,
    "chunk_by_headers": _chunk_by_headers,
}

class CodeTimeout(BaseException):
    """Raised when code execution exceeds the configured timeout.

    Inherits from BaseException (like KeyboardInterrupt) so it cannot be
    caught by the broad ``except Exception`` handler inside the REPL worker.
    """


@dataclass(frozen=True, slots=True)
class IsolatedOpaqueValue:
    """Metadata-only placeholder for a child value that cannot cross JSON IPC."""

    type_name: str


class _UnsupportedWireValue(TypeError):
    """Raised when a REPL value is outside the bounded plain-data contract."""


class _BoundedTextBuffer(io.StringIO):
    """Capture stdout without allowing an unbounded in-memory buffer."""

    def __init__(self, limit: int) -> None:
        super().__init__()
        self._limit = limit
        self._written = 0
        self.truncated = False

    def write(self, value: str) -> int:
        requested = len(value)
        remaining = max(0, self._limit - self._written)
        if remaining:
            super().write(value[:remaining])
            self._written += min(requested, remaining)
        if requested > remaining:
            self.truncated = True
        return requested

    def bounded_value(self) -> str:
        value = self.getvalue()
        if not self.truncated:
            return value
        marker = f"\n... [truncated at {self._limit} chars]"
        if len(marker) >= self._limit:
            return marker[-self._limit :]
        return value[: self._limit - len(marker)] + marker


def _safe_type_name(value: Any) -> str:
    try:
        name = type.__getattribute__(type(value), "__name__")
    except BaseException:  # pragma: no cover - exotic extension/metaclass defense
        return "object"
    return name if isinstance(name, str) else "object"


def _encode_wire_value(
    value: Any,
    *,
    active: set[int] | None = None,
    budget: list[int] | None = None,
    depth: int = 0,
) -> dict[str, Any]:
    """Encode exact built-in values without invoking candidate-defined hooks."""
    if depth > 32:
        raise _UnsupportedWireValue("value nesting is too deep")
    budget = budget if budget is not None else [20_000]
    if budget[0] <= 0:
        raise _UnsupportedWireValue("value graph is too large")
    budget[0] -= 1
    value_type = type(value)
    if value is None:
        return {"type": "none"}
    if value_type is bool:
        return {"type": "bool", "value": value}
    if value_type is int:
        if value.bit_length() > 4096:
            raise _UnsupportedWireValue("integer is too large")
        return {"type": "int", "value": str(value)}
    if value_type is float:
        if not math.isfinite(value):
            raise _UnsupportedWireValue("non-finite float")
        return {"type": "float", "value": value}
    if value_type is str:
        return {"type": "str", "value": value}
    if value_type is bytes:
        return {"type": "bytes", "value": value.hex()}
    if value_type is bytearray:
        return {"type": "bytearray", "value": bytes(value).hex()}
    if value_type is complex:
        if not math.isfinite(value.real) or not math.isfinite(value.imag):
            raise _UnsupportedWireValue("non-finite complex")
        return {"type": "complex", "real": value.real, "imag": value.imag}
    if value_type is range:
        return {
            "type": "range",
            "start": value.start,
            "stop": value.stop,
            "step": value.step,
        }
    if value_type not in {list, tuple, dict, set, frozenset}:
        raise _UnsupportedWireValue(_safe_type_name(value))

    active = active if active is not None else set()
    value_id = id(value)
    if value_id in active:
        raise _UnsupportedWireValue("cyclic value graph")
    active.add(value_id)
    try:
        if value_type is dict:
            dict_items = [
                [
                    _encode_wire_value(key, active=active, budget=budget, depth=depth + 1),
                    _encode_wire_value(item, active=active, budget=budget, depth=depth + 1),
                ]
                for key, item in value.items()
            ]
            return {"type": "dict", "items": dict_items}
        sequence_items = [
            _encode_wire_value(item, active=active, budget=budget, depth=depth + 1)
            for item in value
        ]
        return {"type": value_type.__name__, "items": sequence_items}
    finally:
        active.remove(value_id)


def _decode_wire_value(raw: Any, *, depth: int = 0) -> Any:
    """Strictly decode child data without pickle or dynamic constructors."""
    if depth > 32 or not isinstance(raw, dict):
        raise ValueError("invalid isolated REPL value")
    value_type = raw.get("type")
    if value_type == "none":
        return None
    if value_type == "bool" and isinstance(raw.get("value"), bool):
        return raw["value"]
    if value_type == "int" and isinstance(raw.get("value"), str):
        decoded_int = int(raw["value"])
        if decoded_int.bit_length() <= 4096:
            return decoded_int
    if value_type == "float" and isinstance(raw.get("value"), (int, float)):
        decoded_float = float(raw["value"])
        if math.isfinite(decoded_float):
            return decoded_float
    if value_type == "str" and isinstance(raw.get("value"), str):
        return raw["value"]
    if value_type in {"bytes", "bytearray"} and isinstance(raw.get("value"), str):
        try:
            decoded_bytes = bytes.fromhex(raw["value"])
        except ValueError:
            pass
        else:
            return decoded_bytes if value_type == "bytes" else bytearray(decoded_bytes)
    if value_type == "complex" and isinstance(raw.get("real"), (int, float)) and isinstance(
        raw.get("imag"), (int, float)
    ):
        decoded_complex = complex(float(raw["real"]), float(raw["imag"]))
        if math.isfinite(decoded_complex.real) and math.isfinite(decoded_complex.imag):
            return decoded_complex
    if value_type == "range" and all(isinstance(raw.get(name), int) for name in ("start", "stop", "step")):
        if raw["step"] != 0:
            return range(raw["start"], raw["stop"], raw["step"])
    if value_type == "dict" and isinstance(raw.get("items"), list):
        result: dict[Any, Any] = {}
        for pair in raw["items"]:
            if not isinstance(pair, list) or len(pair) != 2:
                raise ValueError("invalid isolated REPL mapping")
            result[_decode_wire_value(pair[0], depth=depth + 1)] = _decode_wire_value(
                pair[1], depth=depth + 1
            )
        return result
    if value_type in {"list", "tuple", "set", "frozenset"} and isinstance(raw.get("items"), list):
        items = [_decode_wire_value(item, depth=depth + 1) for item in raw["items"]]
        if value_type == "list":
            return items
        if value_type == "tuple":
            return tuple(items)
        if value_type == "set":
            return set(items)
        return frozenset(items)
    raise ValueError("invalid isolated REPL value")


class _ModuleFacade:
    """Read-only attribute facade over an explicit module capability list."""

    __slots__ = ("_members", "_module_name")

    def __init__(self, module_name: str, members: dict[str, Any]) -> None:
        object.__setattr__(self, "_module_name", module_name)
        object.__setattr__(self, "_members", MappingProxyType(dict(members)))

    def __getattribute__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        members = object.__getattribute__(self, "_members")
        try:
            return members[name]
        except KeyError:
            raise AttributeError(name) from None

    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError("module facade is read-only")

    def __delattr__(self, name: str) -> None:
        raise AttributeError("module facade is read-only")

    def __repr__(self) -> str:
        module_name = object.__getattribute__(self, "_module_name")
        return f"<safe module facade {module_name}>"


def _module_facade(module_name: str, module: Any, names: tuple[str, ...]) -> _ModuleFacade:
    return _ModuleFacade(module_name, {name: getattr(module, name) for name in names})


def _build_safe_modules() -> dict[str, _ModuleFacade]:
    """Build fresh immutable facades without paths to module globals/builtins."""
    return {
        "json": _ModuleFacade(
            "json",
            {
                "JSONDecodeError": _json.JSONDecodeError,
                "dumps": _json.dumps,
                "loads": _json.loads,
            },
        ),
        "math": _module_facade("math", math, _MATH_FACADE_NAMES),
        "statistics": _module_facade("statistics", _statistics, _STATISTICS_FACADE_NAMES),
        "collections": _module_facade("collections", _collections, _COLLECTIONS_FACADE_NAMES),
        "re": _module_facade("re", _re, _RE_FACADE_NAMES),
        "time": _module_facade("time", _time, _TIME_FACADE_NAMES),
    }


def _build_restricted_builtins() -> dict[str, Any]:
    """Build an explicit positive allowlist of Python builtins."""
    import builtins as _builtins

    return {name: getattr(_builtins, name) for name in _SAFE_BUILTIN_NAMES}


def _run_repl_command_in_child(
    code: str,
    namespace: dict[str, Any],
    protected_names: frozenset[str],
    baseline_ids: dict[str, int],
    max_stdout_chars: int,
) -> dict[str, Any]:
    """Execute one command and serialize plain namespace changes in the child."""
    unavailable_names: set[str] = set()
    for name, value in list(namespace.items()):
        if isinstance(value, IsolatedOpaqueValue):
            unavailable_names.add(name)
            del namespace[name]

    module = ast.parse(code, mode="exec")
    body = list(module.body)
    trailing_expr: ast.Expr | None = None
    if body and isinstance(body[-1], ast.Expr):
        trailing_expr = body.pop()  # type: ignore[assignment]

    stdout_buf = _BoundedTextBuffer(max_stdout_chars)
    error: str | None = None
    result_repr: str | None = None
    try:
        with contextlib.redirect_stdout(stdout_buf):
            if body:
                exec_mod = ast.Module(body=body, type_ignores=[])
                exec(compile(exec_mod, "<rlm>", "exec"), namespace, namespace)  # noqa: S102
            if trailing_expr is not None:
                value = eval(  # noqa: S307
                    compile(ast.Expression(trailing_expr.value), "<rlm>", "eval"),
                    namespace,
                    namespace,
                )
                if value is not None:
                    result_repr = repr(value)
    except BaseException:  # all candidate exceptions stay inside the child boundary
        import traceback

        error = traceback.format_exc(limit=20)[-16_384:]

    stdout = stdout_buf.bounded_value()
    if result_repr:
        combined = (stdout + "\n" + result_repr).lstrip("\n") if stdout else result_repr
        if len(combined) > max_stdout_chars:
            marker = f"\n... [truncated at {max_stdout_chars} chars]"
            stdout = combined[: max(0, max_stdout_chars - len(marker))] + marker
        else:
            stdout = combined

    present_names: list[str] = []
    updates: dict[str, dict[str, Any]] = {}
    for name, value in namespace.items():
        if name in protected_names or name.startswith("__"):
            continue
        present_names.append(name)
        try:
            updates[name] = {"kind": "value", "value": _encode_wire_value(value)}
        except _UnsupportedWireValue:
            if baseline_ids.get(name) != id(value):
                updates[name] = {"kind": "opaque", "type_name": _safe_type_name(value)}
    present_names.extend(sorted(unavailable_names.difference(present_names)))

    return {
        "stdout": stdout,
        "error": error,
        "present_names": present_names,
        "updates": updates,
    }


class ReplWorker:
    """Restricted Python REPL whose commands run in killable child processes.

    Plain built-in values cross the boundary through bounded tagged JSON so common
    multi-turn state persists. Candidate-created functions, classes, generators,
    and instances never enter the parent; they are represented by metadata-only
    placeholders and are unavailable to later commands.

    This remains local defense in depth, not an OS security sandbox. The child runs
    as the invoking user; use Monty or an external sandbox for mutually untrusted
    tenants that require filesystem and network isolation.
    """

    def __init__(
        self,
        namespace: dict[str, Any] | None = None,
        max_stdout_chars: int = 8192,
        timeout_seconds: float = 10.0,
        max_memory_mb: int = DEFAULT_MAX_MEMORY_MB,
        max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
    ) -> None:
        self._max_stdout = max_stdout_chars
        self._timeout = timeout_seconds
        self._max_memory_mb = max_memory_mb
        self._max_output_bytes = max_output_bytes

        self._namespace: dict[str, Any] = {
            "__name__": "__rlm_repl__",
            "__builtins__": _build_restricted_builtins(),
        }
        self._namespace.update(_build_safe_modules())
        self._namespace.update(_TEXT_HELPERS)
        self._namespace["answer"] = {"content": "", "ready": False}

        if namespace:
            self._namespace.update(namespace)
        self._protected_names = frozenset({
            "__name__",
            "__builtins__",
            *_SAFE_MODULE_NAMES,
            *_TEXT_HELPERS,
        })

    @property
    def namespace(self) -> dict[str, Any]:
        return self._namespace

    def run_code(self, command: ReplCommand) -> ReplResult:
        """Execute *command* outside the parent and apply validated plain-data changes."""
        try:
            ast.parse(command.code, mode="exec")
        except SyntaxError as exc:
            return ReplResult(
                stdout="",
                error=f"SyntaxError: {exc}",
                answer=self._current_answer(),
            )
        violations = check_ast_safety(command.code)
        if violations:
            detail = "; ".join(violations[:10])[:2_000]
            return ReplResult(
                stdout="",
                error=f"AstSafetyError: {detail}",
                answer=self._current_answer(),
            )
        try:
            baseline_ids = {name: id(value) for name, value in self._namespace.items()}
            raw = run_isolated_json(
                lambda: _run_repl_command_in_child(
                    command.code,
                    self._namespace,
                    self._protected_names,
                    baseline_ids,
                    self._max_stdout,
                ),
                timeout_seconds=self._timeout,
                max_memory_mb=self._max_memory_mb,
                max_output_bytes=self._max_output_bytes,
            )
        except IsolatedExecutionTimeout:
            raise CodeTimeout(f"Code execution exceeded {self._timeout}s timeout") from None
        except IsolationUnavailableError:
            return ReplResult(
                stdout="",
                error="IsolationUnavailableError: local child isolation is unavailable",
                answer=self._current_answer(),
            )
        except IsolatedExecutionError:
            logger.debug("harness.repl.worker: isolated execution failed", exc_info=True)
            return ReplResult(
                stdout="",
                error="IsolatedExecutionError: child execution failed",
                answer=self._current_answer(),
            )

        try:
            stdout, error = self._apply_child_result(raw)
        except (TypeError, ValueError):
            logger.debug("harness.repl.worker: invalid child response", exc_info=True)
            return ReplResult(
                stdout="",
                error="IsolatedExecutionError: child returned an invalid result",
                answer=self._current_answer(),
            )
        return ReplResult(stdout=stdout, error=error, answer=self._current_answer())

    def _current_answer(self) -> dict[str, Any]:
        answer = self._namespace.get("answer")
        if type(answer) is not dict:
            return {"content": "", "ready": False}
        return dict(answer)

    def _apply_child_result(self, raw: Any) -> tuple[str, str | None]:
        if not isinstance(raw, dict):
            raise ValueError("isolated REPL result must be an object")
        stdout = raw.get("stdout")
        error = raw.get("error")
        present_names = raw.get("present_names")
        updates = raw.get("updates")
        if not isinstance(stdout, str) or (error is not None and not isinstance(error, str)):
            raise ValueError("invalid isolated REPL output")
        if not isinstance(present_names, list) or not all(isinstance(name, str) for name in present_names):
            raise ValueError("invalid isolated REPL namespace")
        if not isinstance(updates, dict) or not all(isinstance(name, str) for name in updates):
            raise ValueError("invalid isolated REPL updates")

        present = set(present_names)
        for name in list(self._namespace):
            if name not in self._protected_names and not name.startswith("__") and name not in present:
                del self._namespace[name]
        for name, update in updates.items():
            if name in self._protected_names or name.startswith("__") or not isinstance(update, dict):
                raise ValueError("invalid isolated REPL update")
            kind = update.get("kind")
            if kind == "value":
                self._namespace[name] = _decode_wire_value(update.get("value"))
            elif kind == "opaque" and isinstance(update.get("type_name"), str):
                self._namespace[name] = IsolatedOpaqueValue(update["type_name"][:128])
            else:
                raise ValueError("invalid isolated REPL update")
        return stdout, error
