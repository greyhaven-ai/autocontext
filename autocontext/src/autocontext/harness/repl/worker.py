"""Domain-agnostic REPL worker with sandboxed execution."""

from __future__ import annotations

import ast
import contextlib
import io
import signal
import sys
import threading
from typing import Any

from autocontext.harness.repl.types import ReplCommand, ReplResult

_SAFE_MODULES = {
    "json": __import__("json"),
    "math": __import__("math"),
    "statistics": __import__("statistics"),
    "collections": __import__("collections"),
    "re": __import__("re"),
    "time": __import__("time"),
}


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

_BLOCKED_NAMES = frozenset({
    "open",
    "os",
    "sys",
    "subprocess",
    "importlib",
    "__import__",
    "eval",
    "compile",
    "breakpoint",
    "exit",
    "quit",
})


class CodeTimeout(BaseException):
    """Raised when code execution exceeds the configured timeout.

    Inherits from BaseException (like KeyboardInterrupt) so it cannot be
    caught by the broad ``except Exception`` handler inside the REPL worker.
    """


def _build_restricted_builtins() -> dict[str, Any]:
    """Build a builtins dict that excludes dangerous functions."""
    import builtins as _builtins

    safe = {}
    for name in dir(_builtins):
        if name.startswith("_") and name != "__name__":
            continue
        if name in _BLOCKED_NAMES:
            continue
        safe[name] = getattr(_builtins, name)
    return safe


class ReplWorker:
    """In-process Python REPL with an isolated namespace.

    Executes code strings via ``ast.parse`` + compiled code objects in a persistent
    namespace. The namespace is pre-populated with safe standard-library modules and
    an ``answer`` dict that the model uses to return its final output.

    Note: This intentionally uses Python's exec/eval builtins to run LLM-generated
    exploration code (data slicing, filtering, aggregation) in a restricted namespace.
    The namespace excludes file I/O, os, subprocess, and import machinery.
    """

    def __init__(
        self,
        namespace: dict[str, Any] | None = None,
        max_stdout_chars: int = 8192,
        timeout_seconds: float = 10.0,
    ) -> None:
        self._max_stdout = max_stdout_chars
        self._timeout = timeout_seconds

        self._namespace: dict[str, Any] = {
            "__name__": "__rlm_repl__",
            "__builtins__": _build_restricted_builtins(),
        }
        self._namespace.update(_SAFE_MODULES)
        self._namespace.update(_TEXT_HELPERS)
        self._namespace["answer"] = {"content": "", "ready": False}

        if namespace:
            self._namespace.update(namespace)

    @property
    def namespace(self) -> dict[str, Any]:
        return self._namespace

    def run_code(self, command: ReplCommand) -> ReplResult:
        """Execute *command* in the persistent namespace and return captured output."""
        stdout_buf = io.StringIO()
        error: str | None = None

        try:
            module = ast.parse(command.code, mode="exec")
        except SyntaxError as exc:
            return ReplResult(
                stdout="",
                error=f"SyntaxError: {exc}",
                answer=dict(self._namespace.get("answer", {"content": "", "ready": False})),
            )

        # Split trailing expression so its repr is captured.
        body = list(module.body)
        trailing_expr: ast.Expr | None = None
        if body and isinstance(body[-1], ast.Expr):
            trailing_expr = body.pop()  # type: ignore[assignment]

        def _run() -> str | None:
            nonlocal error
            result_repr: str | None = None
            try:
                with contextlib.redirect_stdout(stdout_buf):
                    if body:
                        exec_mod = ast.Module(body=body, type_ignores=[])
                        # Intentional: runs LLM code in restricted namespace (no file I/O, no os, no imports)
                        exec(compile(exec_mod, "<rlm>", "exec"), self._namespace, self._namespace)  # noqa: S102
                    if trailing_expr is not None:
                        value = eval(  # noqa: S307
                            compile(ast.Expression(trailing_expr.value), "<rlm>", "eval"),
                            self._namespace,
                            self._namespace,
                        )
                        if value is not None:
                            result_repr = repr(value)
            except Exception:  # noqa: BLE001
                import traceback

                error = traceback.format_exc()
            return result_repr

        result_repr = self._execute_with_timeout(_run)

        stdout = stdout_buf.getvalue()
        if result_repr:
            stdout = (stdout + "\n" + result_repr).lstrip("\n") if stdout else result_repr
        if len(stdout) > self._max_stdout:
            stdout = stdout[: self._max_stdout] + f"\n... [truncated at {self._max_stdout} chars]"

        answer = dict(self._namespace.get("answer", {"content": "", "ready": False}))
        return ReplResult(stdout=stdout, error=error, answer=answer)

    def _execute_with_timeout(self, fn: Any) -> Any:
        """Run *fn* with a wall-clock timeout."""
        if sys.platform != "win32" and threading.current_thread() is threading.main_thread():
            return self._timeout_via_signal(fn)
        return self._timeout_via_thread(fn)

    def _timeout_via_signal(self, fn: Any) -> Any:
        def _handler(signum: int, frame: Any) -> None:
            raise CodeTimeout(f"Code execution exceeded {self._timeout}s timeout")

        old = signal.signal(signal.SIGALRM, _handler)
        signal.setitimer(signal.ITIMER_REAL, self._timeout)
        try:
            return fn()
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, old)

    def _timeout_via_thread(self, fn: Any) -> Any:
        result: list[Any] = [None]
        exc_holder: list[BaseException | None] = [None]

        def _target() -> None:
            try:
                result[0] = fn()
            except BaseException as e:  # noqa: BLE001
                exc_holder[0] = e

        t = threading.Thread(target=_target, daemon=True)
        t.start()
        t.join(timeout=self._timeout)
        if t.is_alive():
            raise CodeTimeout(f"Code execution exceeded {self._timeout}s timeout")
        if exc_holder[0] is not None:
            raise exc_holder[0]
        return result[0]
