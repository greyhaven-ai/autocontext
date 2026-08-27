"""Persistent interpreter workspace for multi-generation runs (AC-901).

Working state (candidate pools, seeds, helper data) lives in a persistent
Python namespace that survives generation boundaries; enriched prompts
describe the variables (name, type, size, summary) instead of inlining
their contents, so prompt size stays flat as the working set grows.

Trust model: candidate code runs in a fresh killable local child and only
plain built-in state returns over bounded JSON. This prevents parent-process
mutation and runaway-thread survival, but it is not a filesystem or network
sandbox because the child still has the invoking user's OS identity.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any

from autocontext.harness.repl.types import ReplCommand, ReplResult
from autocontext.harness.repl.worker import CodeTimeout, IsolatedOpaqueValue, ReplWorker

_SUMMARY_MAX_CHARS = 120
_SUMMARY_MAX_ITEMS = 20
_SUMMARY_MAX_DEPTH = 6
_SUMMARY_MAX_NODES = 200
_SAFE_ATOMIC_TYPES = frozenset({type(None), bool, int, float, complex, str, bytes, range})
_SAFE_SIZED_TYPES = frozenset({str, bytes, bytearray, range, list, tuple, dict, set, frozenset})


class _UnsupportedWorkspaceValue(TypeError):
    """Raised when migration would have to execute candidate-defined code."""


def _type_name(value: Any) -> str:
    """Read a type name without dispatching through a candidate metaclass."""
    if type(value) is IsolatedOpaqueValue:
        opaque_name = object.__getattribute__(value, "type_name")
        return opaque_name if isinstance(opaque_name, str) else "object"
    value_type = type(value)
    try:
        name = type.__getattribute__(value_type, "__name__")
        return name if isinstance(name, str) else "object"
    except KeyboardInterrupt:
        raise
    except BaseException:  # pragma: no cover - defensive against exotic extension types
        return "object"


def _safe_summary(
    value: Any,
    *,
    seen: set[int] | None = None,
    budget: list[int] | None = None,
    depth: int = 0,
) -> str:
    """Render bounded metadata without calling candidate-defined magic methods."""
    budget = budget if budget is not None else [_SUMMARY_MAX_NODES]
    if budget[0] <= 0:
        return "..."
    budget[0] -= 1
    value_type = type(value)
    if value_type is IsolatedOpaqueValue:
        return f"<{object.__getattribute__(value, 'type_name')}>"
    if value_type is str:
        clipped = value[:_SUMMARY_MAX_CHARS]
        return repr(clipped) + ("..." if len(value) > len(clipped) else "")
    if value_type in {bytes, bytearray}:
        clipped = value[:_SUMMARY_MAX_CHARS]
        return repr(clipped) + ("..." if len(value) > len(clipped) else "")
    if value_type is int and value.bit_length() > 4096:
        return "<int>"
    if value_type in _SAFE_ATOMIC_TYPES:
        return repr(value)

    if value_type not in {list, tuple, dict, set, frozenset}:
        return f"<{_type_name(value)}>"
    if depth >= _SUMMARY_MAX_DEPTH or len(value) > _SUMMARY_MAX_ITEMS:
        return f"<{_type_name(value)}>"

    seen = seen if seen is not None else set()
    value_id = id(value)
    if value_id in seen:
        return "..."
    seen.add(value_id)
    try:
        if value_type is list:
            return (
                "["
                + ", ".join(_safe_summary(item, seen=seen, budget=budget, depth=depth + 1) for item in value)
                + "]"
            )
        if value_type is tuple:
            rendered = ", ".join(
                _safe_summary(item, seen=seen, budget=budget, depth=depth + 1) for item in value
            )
            return f"({rendered}{',' if len(value) == 1 else ''})"
        if value_type is dict:
            rendered = ", ".join(
                f"{_safe_summary(key, seen=seen, budget=budget, depth=depth + 1)}: "
                f"{_safe_summary(item, seen=seen, budget=budget, depth=depth + 1)}"
                for key, item in value.items()
            )
            return "{" + rendered + "}"
        rendered = ", ".join(
            _safe_summary(item, seen=seen, budget=budget, depth=depth + 1) for item in value
        )
        if value_type is set:
            return "set()" if not value else "{" + rendered + "}"
        return f"frozenset({{{rendered}}})"
    finally:
        seen.remove(value_id)


def _safe_clone(value: Any, *, memo: dict[int, Any] | None = None, active: set[int] | None = None) -> Any:
    """Clone plain built-in data recursively without invoking ``__deepcopy__``."""
    value_type = type(value)
    if value_type in _SAFE_ATOMIC_TYPES:
        return value
    if value_type is bytearray:
        return bytearray(value)
    if value_type not in {list, tuple, dict, set, frozenset}:
        raise _UnsupportedWorkspaceValue(_type_name(value))

    memo = memo if memo is not None else {}
    active = active if active is not None else set()
    value_id = id(value)
    if value_id in memo:
        return memo[value_id]
    if value_id in active:
        raise _UnsupportedWorkspaceValue("cyclic immutable container")
    active.add(value_id)
    try:
        if value_type is list:
            cloned_list: list[Any] = []
            memo[value_id] = cloned_list
            cloned_list.extend(_safe_clone(item, memo=memo, active=active) for item in value)
            return cloned_list
        if value_type is dict:
            cloned_dict: dict[Any, Any] = {}
            memo[value_id] = cloned_dict
            for key, item in value.items():
                cloned_dict[_safe_clone(key, memo=memo, active=active)] = _safe_clone(item, memo=memo, active=active)
            return cloned_dict
        if value_type is set:
            cloned_set: set[Any] = set()
            memo[value_id] = cloned_set
            cloned_set.update(_safe_clone(item, memo=memo, active=active) for item in value)
            return cloned_set
        if value_type is tuple:
            cloned_tuple = tuple(_safe_clone(item, memo=memo, active=active) for item in value)
            memo[value_id] = cloned_tuple
            return cloned_tuple
        cloned_frozen = frozenset(_safe_clone(item, memo=memo, active=active) for item in value)
        memo[value_id] = cloned_frozen
        return cloned_frozen
    finally:
        active.remove(value_id)


@dataclass(frozen=True, slots=True)
class WorkspaceVariable:
    """Prompt-facing description of one workspace variable."""

    name: str
    type_name: str
    size: int | None
    summary: str


@dataclass(slots=True)
class WorkspaceSnapshot:
    """Safely copied user variables captured for migration or inspection.

    ``skipped`` records values outside the plain built-in data contract
    (generators, custom instances, open handles, ...). Candidate-defined copy
    hooks are never executed; unsupported values degrade to omission.
    """

    variables: dict[str, Any]
    skipped: tuple[str, ...] = ()


class InterpreterWorkspace:
    """A persistent, restricted Python namespace owned by a runner lineage.

    Variables assigned by executed code (and any ``seed`` values) persist
    across :meth:`run` calls until :meth:`close`. Names starting with an
    underscore are treated as private scratch and excluded from
    :meth:`variables`, snapshots, and prompt rendering.

    The local child boundary is POSIX-only and may only fork from the process
    main thread. Unsupported platforms and worker-thread callers fail closed.
    Candidate-created opaque Python objects are metadata-only in the parent
    and cannot be used by a later command; plain built-in values persist.
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
                except KeyboardInterrupt:
                    raise
                except BaseException:  # noqa: BLE001
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
        except KeyboardInterrupt:
            # Deliberately NOT contained: a genuine operator signal, not
            # candidate behavior.
            raise
        except BaseException as exc:  # noqa: BLE001
            # ReplWorker's inner handler catches only Exception, so a
            # candidate's `raise SystemExit(...)` or `raise BaseException(...)`
            # would otherwise escape and kill the owning process (TaskRunner
            # catches Exception only). Containment of every non-operator
            # escape is the point of this handler.
            return ReplResult(stdout="", error=f"{type(exc).__name__}: {exc}", answer={})

    def variables(self) -> list[WorkspaceVariable]:
        """Describe user variables (never their full contents), sorted by name."""
        self._ensure_open()
        described: list[WorkspaceVariable] = []
        for name, value in self._user_items():
            value_type = type(value)
            try:
                size = len(value) if value_type in _SAFE_SIZED_TYPES else None
                summary = _safe_summary(value)
            except KeyboardInterrupt:
                raise
            except BaseException:  # noqa: BLE001
                size = None
                summary = f"<{_type_name(value)}>"
            if len(summary) > _SUMMARY_MAX_CHARS:
                summary = summary[: _SUMMARY_MAX_CHARS - 3] + "..."
            described.append(WorkspaceVariable(name=name, type_name=_type_name(value), size=size, summary=summary))
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
        """Copy plain built-in user variables without executing candidate hooks."""
        self._ensure_open()
        variables: dict[str, Any] = {}
        skipped: list[str] = []
        for name, value in self._user_items():
            try:
                variables[name] = _safe_clone(value)
            except KeyboardInterrupt:
                raise
            except BaseException:  # noqa: BLE001
                skipped.append(name)
        return WorkspaceSnapshot(variables=variables, skipped=tuple(skipped))

    def restore(self, snap: WorkspaceSnapshot) -> None:
        """Replace user variables with a safe copy of the snapshot's.

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
                staged[name] = _safe_clone(value)
            except KeyboardInterrupt:
                raise
            except BaseException:  # noqa: BLE001
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
