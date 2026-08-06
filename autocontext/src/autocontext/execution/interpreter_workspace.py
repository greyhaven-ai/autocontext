"""Persistent interpreter workspace for multi-generation runs (AC-901).

Working state (candidate pools, seeds, helper data) lives in a persistent
Python namespace that survives generation boundaries; enriched prompts
describe the variables (name, type, size, summary) instead of inlining
their contents, so prompt size stays flat as the working set grows.

Trust model: this is lifecycle isolation, NOT a security sandbox. The
underlying :class:`~autocontext.harness.repl.worker.ReplWorker` restricts
builtins (no file I/O, os, subprocess, or import machinery), but candidate
code still runs in-process; treat the workspace as a scoped scratch
environment for run-owned code, not as a boundary against hostile input.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any

from autocontext.harness.repl.types import ReplCommand, ReplResult
from autocontext.harness.repl.worker import CodeTimeout, ReplWorker

_SUMMARY_MAX_CHARS = 120


@dataclass(frozen=True, slots=True)
class WorkspaceVariable:
    """Prompt-facing description of one workspace variable."""

    name: str
    type_name: str
    size: int | None
    summary: str


@dataclass(slots=True)
class WorkspaceSnapshot:
    """Deep-copied user variables captured for migration or inspection.

    ``skipped`` records variables whose values could not be deep-copied
    (generators, open handles, ...); they degrade to omission, never to a
    crash, so migration always proceeds with what is copyable.
    """

    variables: dict[str, Any]
    skipped: tuple[str, ...] = ()


class InterpreterWorkspace:
    """A persistent, restricted Python namespace owned by a runner lineage.

    Variables assigned by executed code (and any ``seed`` values) persist
    across :meth:`run` calls until :meth:`close`. Names starting with an
    underscore are treated as private scratch and excluded from
    :meth:`variables`, snapshots, and prompt rendering.

    Caveat: off the main thread, ReplWorker enforces timeouts by abandoning
    the executing daemon thread; a timed-out candidate may keep mutating
    the persistent namespace in the background. On the main thread (the
    normal runner path) timeouts are signal-based and this does not apply.
    """

    def __init__(
        self,
        seed: dict[str, Any] | None = None,
        *,
        timeout_seconds: float = 10.0,
        max_stdout_chars: int = 8192,
    ) -> None:
        self._worker = ReplWorker(timeout_seconds=timeout_seconds, max_stdout_chars=max_stdout_chars)
        # Captured before seeding: everything ReplWorker pre-installs
        # (builtins, safe modules, text helpers, answer) is infrastructure.
        self._infra_keys = frozenset(self._worker.namespace)
        if seed:
            for name in seed:
                if name in self._infra_keys or name.startswith("_"):
                    raise ValueError(
                        f"seed key {name!r} collides with workspace infrastructure or is private; "
                        "choose a different variable name"
                    )
            # Deep-copied so candidate mutations never leak back into caller
            # state; uncopyable values fall back to the shared reference
            # (dropping an explicitly provided seed would be worse).
            for name, value in seed.items():
                try:
                    self._worker.namespace[name] = copy.deepcopy(value)
                except Exception:
                    self._worker.namespace[name] = value
        self._closed = False

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("workspace is closed")

    def _user_items(self) -> list[tuple[str, Any]]:
        return sorted(
            (name, value)
            for name, value in self._worker.namespace.items()
            if name not in self._infra_keys and not name.startswith("_")
        )

    def run(self, code: str) -> ReplResult:
        """Execute ``code`` in the persistent namespace.

        Timeouts are converted into an error result rather than raised, so a
        runaway candidate never tears down the owning generation loop.
        """
        self._ensure_open()
        try:
            return self._worker.run_code(ReplCommand(code=code))
        except CodeTimeout as exc:
            return ReplResult(stdout="", error=f"CodeTimeout: {exc}", answer={})
        except SystemExit as exc:
            # ReplWorker's inner handler catches only Exception, so a
            # candidate's `raise SystemExit(...)` would otherwise escape and
            # kill the owning process (TaskRunner catches Exception only).
            # KeyboardInterrupt is deliberately NOT contained: that is a
            # genuine operator signal, not candidate behavior.
            return ReplResult(stdout="", error=f"SystemExit: {exc}", answer={})

    def variables(self) -> list[WorkspaceVariable]:
        """Describe user variables (never their full contents), sorted by name."""
        self._ensure_open()
        described: list[WorkspaceVariable] = []
        for name, value in self._user_items():
            try:
                size: int | None = len(value)
            except TypeError:
                size = None
            try:
                summary = repr(value)
            except Exception:
                summary = f"<{type(value).__name__}>"
            if len(summary) > _SUMMARY_MAX_CHARS:
                summary = summary[: _SUMMARY_MAX_CHARS - 3] + "..."
            described.append(WorkspaceVariable(name=name, type_name=type(value).__name__, size=size, summary=summary))
        return described

    def render_markdown(self, max_vars: int = 20) -> str:
        """Render the variable listing as prompt-ready markdown lines."""
        listed = self.variables()
        lines = []
        for var in listed[:max_vars]:
            size_part = f", size {var.size}" if var.size is not None else ""
            lines.append(f"- {var.name} ({var.type_name}{size_part}): {var.summary}")
        if len(listed) > max_vars:
            lines.append(f"... and {len(listed) - max_vars} more")
        return "\n".join(lines)

    def snapshot(self) -> WorkspaceSnapshot:
        """Deep-copy user variables; non-copyable values are skipped, not fatal."""
        self._ensure_open()
        variables: dict[str, Any] = {}
        skipped: list[str] = []
        for name, value in self._user_items():
            try:
                variables[name] = copy.deepcopy(value)
            except Exception:
                skipped.append(name)
        return WorkspaceSnapshot(variables=variables, skipped=tuple(skipped))

    def restore(self, snap: WorkspaceSnapshot) -> None:
        """Replace user variables with a deep copy of the snapshot's.

        The second copy keeps lineages independent: restoring the same
        snapshot into several islands must never share mutable objects.
        Copies are staged before any existing variable is deleted, so a
        value whose deepcopy fails at restore time degrades to omission
        instead of leaving a half-restored namespace.
        """
        self._ensure_open()
        staged: dict[str, Any] = {}
        for name, value in snap.variables.items():
            try:
                staged[name] = copy.deepcopy(value)
            except Exception:
                continue
        for name, _ in self._user_items():
            del self._worker.namespace[name]
        self._worker.namespace.update(staged)

    def close(self) -> None:
        """Deterministic teardown: drop all namespace references. Idempotent."""
        if self._closed:
            return
        self._worker.namespace.clear()
        self._closed = True
