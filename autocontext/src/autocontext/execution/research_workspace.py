"""Capability-scoped persistent research workspaces (AC-977).

The existing :class:`InterpreterWorkspace` remains the default restricted
scratch surface.  This module adds an explicitly approved, process-backed
surface for code/research tasks that need workspace files, selected imports,
bounded subprocesses, or separately granted network access.

This is a capability boundary, not a replacement for an OS/VM sandbox.  The
``isolated_sandbox`` profile is process-isolated and transactional at the
workspace boundary; deployments handling hostile code should place that child
process inside their sandbox adapter as well.
"""

from __future__ import annotations

import ast
import builtins
import contextlib
import io
import multiprocessing
import os
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, TypeAlias

from autocontext.execution.interpreter_workspace import InterpreterWorkspace, WorkspaceVariable
from autocontext.harness.repl.types import ReplResult
from autocontext.runtimes.workspace_env import RuntimeWorkspaceEnv, create_local_workspace_env

WorkspaceProfile: TypeAlias = Literal["restricted_scratch", "trusted_local", "isolated_sandbox"]
WorkspaceCapability: TypeAlias = Literal[
    "workspace_read",
    "workspace_write",
    "package_import",
    "subprocess",
    "network",
    "host_bridge",
]
WorkspaceLifecyclePolicy: TypeAlias = Literal["retain", "delete_on_close"]
CapabilityApprover: TypeAlias = Callable[["WorkspaceCapabilityRequest"], bool]
HostBridge: TypeAlias = Callable[[str, Mapping[str, Any]], Any]

_CAPABILITIES: frozenset[str] = frozenset(
    {"workspace_read", "workspace_write", "package_import", "subprocess", "network", "host_bridge"}
)
_BLOCKED_NAMES = frozenset(
    {
        "breakpoint",
        "compile",
        "delattr",
        "eval",
        "exec",
        "exit",
        "getattr",
        "globals",
        "help",
        "input",
        "locals",
        "memoryview",
        "quit",
        "setattr",
        "vars",
    }
)
_BLOCKED_MODULES = frozenset({"ctypes", "importlib", "multiprocessing", "os", "pathlib", "socket", "subprocess", "sys"})
_INFRA_NAMES = frozenset(
    {
        "__builtins__",
        "__name__",
        "answer",
        "host_call",
        "network_fetch",
        "run_subprocess",
        "workspace_read_bytes",
        "workspace_read_text",
        "workspace_write_bytes",
        "workspace_write_text",
    }
)


@dataclass(frozen=True, slots=True)
class WorkspaceResourceLimits:
    timeout_seconds: float = 10.0
    subprocess_timeout_seconds: float = 10.0
    max_stdout_chars: int = 8192
    max_file_bytes: int = 8 * 1024 * 1024
    max_network_bytes: int = 1024 * 1024

    def __post_init__(self) -> None:
        if self.timeout_seconds <= 0 or self.subprocess_timeout_seconds <= 0:
            raise ValueError("workspace timeouts must be positive")
        if self.max_stdout_chars <= 0 or self.max_file_bytes <= 0 or self.max_network_bytes <= 0:
            raise ValueError("workspace byte limits must be positive")


@dataclass(frozen=True, slots=True)
class WorkspaceCapabilityRequest:
    workspace_id: str
    profile: WorkspaceProfile = "restricted_scratch"
    requested_capabilities: frozenset[WorkspaceCapability] = frozenset()
    allowed_imports: frozenset[str] = frozenset()
    allowed_commands: frozenset[str] = frozenset()
    allowed_network_hosts: frozenset[str] = frozenset()
    limits: WorkspaceResourceLimits = field(default_factory=WorkspaceResourceLimits)
    lifecycle: WorkspaceLifecyclePolicy = "retain"
    approval_context: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.workspace_id.strip():
            raise ValueError("workspace_id must be non-empty")
        unknown = set(self.requested_capabilities) - _CAPABILITIES
        if unknown:
            raise ValueError(f"unknown workspace capabilities: {sorted(unknown)}")
        if self.profile == "restricted_scratch" and self.requested_capabilities:
            raise ValueError("restricted_scratch does not accept elevated capabilities")


@dataclass(frozen=True, slots=True)
class WorkspaceGrant:
    workspace_id: str
    profile: WorkspaceProfile
    requested_capabilities: frozenset[WorkspaceCapability]
    granted_capabilities: frozenset[WorkspaceCapability]
    denied_capabilities: frozenset[WorkspaceCapability]
    limits: WorkspaceResourceLimits
    approval_context: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class WorkspaceAuditEvent:
    sequence: int
    timestamp: float
    workspace_id: str
    profile: WorkspaceProfile
    action: str
    outcome: str
    capabilities: tuple[WorkspaceCapability, ...]
    resource: str = ""
    detail: str = ""


@dataclass(frozen=True, slots=True)
class ResearchWorkspaceSnapshot:
    workspace_id: str
    variables: Mapping[str, Any]
    helper_sources: tuple[str, ...]
    files: Mapping[str, bytes]
    skipped_variables: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class WorkspaceCleanupResult:
    outcome: Literal["retained", "deleted", "already_closed", "error"]
    workspace_root: str
    detail: str = ""


@dataclass(frozen=True, slots=True)
class ResearchWorkspaceBenchmark:
    restricted_task_quality: float
    capable_task_quality: float
    restricted_wall_seconds: float
    capable_wall_seconds: float
    capable_prompt_chars: tuple[int, ...]
    restricted_cleanup: str
    capable_cleanup: str


def grant_workspace_access(
    request: WorkspaceCapabilityRequest,
    approver: CapabilityApprover | None = None,
) -> WorkspaceGrant:
    """Resolve an explicit request. Elevated capabilities default to deny."""

    requested = request.requested_capabilities
    if request.profile == "restricted_scratch":
        granted: frozenset[WorkspaceCapability] = frozenset()
    elif approver is not None and approver(request):
        granted = requested
    else:
        granted = frozenset()
    return WorkspaceGrant(
        workspace_id=request.workspace_id,
        profile=request.profile,
        requested_capabilities=requested,
        granted_capabilities=granted,
        denied_capabilities=requested - granted,
        limits=request.limits,
        approval_context=dict(request.approval_context),
    )


def benchmark_research_workspace() -> ResearchWorkspaceBenchmark:
    """Run a deterministic multi-generation code/research acceptance task.

    Quality is binary: the task succeeds only when it can read the supplied
    observations, retain a helper and derived value, execute one bounded child
    command, and write the declared report. Prompt-size samples demonstrate
    that persisted state is represented as metadata rather than serialized
    observations.
    """

    restricted = ResearchWorkspace(
        WorkspaceCapabilityRequest(workspace_id="benchmark-restricted", lifecycle="delete_on_close")
    )
    restricted_start = time.perf_counter()
    restricted_result = restricted.run("workspace_read_text('observations.txt')")
    restricted_wall = time.perf_counter() - restricted_start
    restricted_cleanup = restricted.close().outcome

    request = WorkspaceCapabilityRequest(
        workspace_id="benchmark-capable",
        profile="isolated_sandbox",
        requested_capabilities=frozenset(
            {"workspace_read", "workspace_write", "package_import", "subprocess"}
        ),
        allowed_imports=frozenset({"statistics"}),
        allowed_commands=frozenset({sys.executable}),
        lifecycle="delete_on_close",
    )
    capable = ResearchWorkspace(request, approver=lambda _: True)
    capable.runtime_env.write_file("observations.txt", "4\n8\n12\n")
    prompt_chars = [len(capable.render_markdown())]
    capable_start = time.perf_counter()
    generation_one = capable.run(
        "import statistics\n"
        "def parse_numbers(text):\n"
        "    return [int(line) for line in text.splitlines() if line]\n"
        "observed_mean = statistics.mean(parse_numbers(workspace_read_text('observations.txt')))"
    )
    prompt_chars.append(len(capable.render_markdown()))
    generation_two = capable.run(
        f"check = run_subprocess([{sys.executable!r}, '-c', 'print(6 * 7)'])\n"
        "check_value = int(check['stdout'])"
    )
    prompt_chars.append(len(capable.render_markdown()))
    generation_three = capable.run(
        "workspace_write_text('report.txt', f'mean={observed_mean}; check={check_value}')"
    )
    capable_wall = time.perf_counter() - capable_start
    quality = float(
        all(result.error is None for result in (generation_one, generation_two, generation_three))
        and capable.runtime_env.read_file("report.txt") == "mean=8; check=42"
    )
    capable_cleanup = capable.close().outcome
    return ResearchWorkspaceBenchmark(
        restricted_task_quality=float(restricted_result.error is None),
        capable_task_quality=quality,
        restricted_wall_seconds=restricted_wall,
        capable_wall_seconds=capable_wall,
        capable_prompt_chars=tuple(prompt_chars),
        restricted_cleanup=restricted_cleanup,
        capable_cleanup=capable_cleanup,
    )


class ResearchWorkspace:
    """Persistent workspace with restricted and explicit capable profiles."""

    def __init__(
        self,
        request: WorkspaceCapabilityRequest,
        *,
        workspace_root: str | Path | None = None,
        approver: CapabilityApprover | None = None,
        host_bridge: HostBridge | None = None,
        seed: Mapping[str, Any] | None = None,
    ) -> None:
        self.request = request
        self.grant = grant_workspace_access(request, approver)
        if self.grant.denied_capabilities:
            denied = ", ".join(sorted(self.grant.denied_capabilities))
            raise PermissionError(f"workspace capabilities were not approved: {denied}")
        self._owned_root = workspace_root is None
        self._root = (
            Path(tempfile.mkdtemp(prefix=f"autocontext-{_safe_id(request.workspace_id)}-"))
            if workspace_root is None
            else Path(workspace_root).resolve()
        ).resolve()
        self._root.mkdir(parents=True, exist_ok=True)
        self.runtime_env: RuntimeWorkspaceEnv = create_local_workspace_env(root=self._root)
        self._host_bridge = host_bridge
        self._restricted = (
            InterpreterWorkspace(seed=dict(seed or {}), timeout_seconds=request.limits.timeout_seconds)
            if request.profile == "restricted_scratch"
            else None
        )
        self._variables: dict[str, Any] = _copy_plain_mapping(seed or {})
        self._helper_sources: list[str] = []
        self._events: list[WorkspaceAuditEvent] = []
        self._closed = False
        self._sequence = 0
        self._record("open", "granted", detail=f"lifecycle={request.lifecycle}")

    @property
    def workspace_root(self) -> Path:
        return self._root

    @property
    def audit_events(self) -> tuple[WorkspaceAuditEvent, ...]:
        return tuple(self._events)

    def run(self, code: str) -> ReplResult:
        """Run one generation, committing state and files only on success."""

        self._ensure_open()
        if self._restricted is not None:
            result = self._restricted.run(code)
            self._record("execute", "success" if result.error is None else "candidate_error")
            return result

        try:
            _validate_capable_code(code, self.request.allowed_imports)
        except PermissionError as exc:
            self._record("execute", "denied", detail=str(exc))
            return ReplResult(stdout="", error=f"PermissionError: {exc}", answer={})
        staging = Path(tempfile.mkdtemp(prefix=".autocontext-stage-", dir=self._root.parent)).resolve()
        try:
            _copy_workspace(self._root, staging, self.request.limits.max_file_bytes)
            payload = {
                "code": code,
                "variables": self._variables,
                "helper_sources": tuple(self._helper_sources),
                "workspace_root": str(staging),
                "capabilities": tuple(self.grant.granted_capabilities),
                "allowed_imports": tuple(self.request.allowed_imports),
                "allowed_commands": tuple(self.request.allowed_commands),
                "allowed_network_hosts": tuple(self.request.allowed_network_hosts),
                "limits": self.request.limits,
            }
            response = _run_in_child(payload, self.request.limits.timeout_seconds)
            if response is None:
                self._record("execute", "timeout", detail="child process terminated")
                return ReplResult(stdout="", error="CodeTimeout: child process terminated", answer={})
            result = ReplResult(
                stdout=str(response.get("stdout", "")),
                error=str(response["error"]) if response.get("error") else None,
                answer=dict(response.get("answer", {})),
            )
            if result.error is None:
                self._variables = _copy_plain_mapping(response.get("variables", {}))
                self._helper_sources.extend(_new_helper_sources(code, self._helper_sources))
                _replace_workspace(staging, self._root, self.request.limits.max_file_bytes)
                self._record("execute", "success", detail=_resource_detail(response))
            else:
                self._record("execute", "candidate_error", detail=result.error[-240:])
            for call in response.get("host_calls", ()):
                self._record("host_bridge", "requested", resource=str(call.get("name", "")))
            return result
        finally:
            shutil.rmtree(staging, ignore_errors=True)

    def host_call(self, name: str, arguments: Mapping[str, Any]) -> Any:
        """Invoke a typed host-plane operation outside the candidate kernel."""

        self._ensure_open()
        if "host_bridge" not in self.grant.granted_capabilities:
            self._record("host_bridge", "denied", resource=name)
            raise PermissionError("host_bridge capability was not granted")
        if self._host_bridge is None:
            self._record("host_bridge", "unavailable", resource=name)
            raise RuntimeError("no host bridge is configured")
        result = self._host_bridge(name, dict(arguments))
        self._record("host_bridge", "success", resource=name)
        return result

    def variables(self) -> list[WorkspaceVariable]:
        self._ensure_open()
        if self._restricted is not None:
            return self._restricted.variables()
        variables: list[WorkspaceVariable] = []
        for name, value in sorted(self._variables.items()):
            summary = repr(value)
            variables.append(
                WorkspaceVariable(
                    name=name,
                    type_name=type(value).__name__,
                    size=len(value) if type(value) in {str, bytes, bytearray, list, tuple, dict, set, frozenset} else None,
                    summary=summary[:117] + "..." if len(summary) > 120 else summary,
                )
            )
        for source in self._helper_sources:
            node = ast.parse(source).body[0]
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                variables.append(WorkspaceVariable(name=node.name, type_name="function", size=None, summary="<function>"))
        return sorted(variables, key=lambda item: item.name)

    def render_markdown(self, max_vars: int = 20) -> str:
        listed = self.variables()
        lines = [
            f"- profile: {self.request.profile}",
            f"- grants: {', '.join(sorted(self.grant.granted_capabilities)) or 'none'}",
        ]
        for item in listed[:max_vars]:
            size = f", size {item.size}" if item.size is not None else ""
            lines.append(f"- {item.name} ({item.type_name}{size}): {item.summary}")
        if len(listed) > max_vars:
            lines.append(f"... and {len(listed) - max_vars} more")
        return "\n".join(lines)

    def snapshot(self) -> ResearchWorkspaceSnapshot:
        self._ensure_open()
        if self._restricted is not None:
            snap = self._restricted.snapshot()
            variables = snap.variables
            skipped = snap.skipped
        else:
            variables = _copy_plain_mapping(self._variables)
            skipped = ()
        snapshot = ResearchWorkspaceSnapshot(
            workspace_id=self.request.workspace_id,
            variables=variables,
            helper_sources=tuple(self._helper_sources),
            files=_snapshot_files(self._root, self.request.limits.max_file_bytes),
            skipped_variables=skipped,
        )
        self._record("snapshot", "success", detail=f"files={len(snapshot.files)}")
        return snapshot

    def restore(self, snapshot: ResearchWorkspaceSnapshot) -> None:
        self._ensure_open()
        if snapshot.workspace_id != self.request.workspace_id:
            raise ValueError("snapshot belongs to a different workspace")
        if self._restricted is not None:
            from autocontext.execution.interpreter_workspace import WorkspaceSnapshot

            self._restricted.restore(WorkspaceSnapshot(variables=dict(snapshot.variables)))
        else:
            self._variables = _copy_plain_mapping(snapshot.variables)
            self._helper_sources = list(snapshot.helper_sources)
        _restore_files(self._root, snapshot.files, self.request.limits.max_file_bytes)
        self._record("restore", "success", detail=f"files={len(snapshot.files)}")

    def close(self) -> WorkspaceCleanupResult:
        if self._closed:
            return WorkspaceCleanupResult("already_closed", str(self._root))
        if self._restricted is not None:
            self._restricted.close()
        self.runtime_env.cleanup()
        outcome: Literal["retained", "deleted", "error"] = "retained"
        detail = ""
        if self.request.lifecycle == "delete_on_close" and self._owned_root:
            try:
                shutil.rmtree(self._root)
                outcome = "deleted"
            except OSError as exc:
                outcome = "error"
                detail = str(exc)
        self._record("cleanup", outcome, detail=detail)
        self._closed = True
        return WorkspaceCleanupResult(outcome, str(self._root), detail)

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("workspace is closed")

    def _record(self, action: str, outcome: str, *, resource: str = "", detail: str = "") -> None:
        self._sequence += 1
        self._events.append(
            WorkspaceAuditEvent(
                sequence=self._sequence,
                timestamp=time.time(),
                workspace_id=self.request.workspace_id,
                profile=self.request.profile,
                action=action,
                outcome=outcome,
                capabilities=tuple(sorted(self.grant.granted_capabilities)),
                resource=resource,
                detail=detail,
            )
        )


def _run_in_child(payload: Mapping[str, Any], timeout_seconds: float) -> dict[str, Any] | None:
    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe(duplex=False)
    process = context.Process(target=_child_entrypoint, args=(child, dict(payload)), daemon=True)
    process.start()
    child.close()
    try:
        if not parent.poll(timeout_seconds):
            process.terminate()
            process.join(timeout=1.0)
            if process.is_alive():
                process.kill()
                process.join(timeout=1.0)
            return None
        response = parent.recv()
        process.join(timeout=1.0)
        return response if isinstance(response, dict) else {"error": "invalid child response"}
    finally:
        parent.close()
        if process.is_alive():
            process.terminate()
            process.join(timeout=1.0)


def _child_entrypoint(connection: Any, payload: dict[str, Any]) -> None:
    try:
        connection.send(_execute_child(payload))
    except BaseException as exc:  # noqa: BLE001
        connection.send({"stdout": "", "error": f"{type(exc).__name__}: {exc}", "answer": {}})
    finally:
        connection.close()


def _execute_child(payload: Mapping[str, Any]) -> dict[str, Any]:
    capabilities = frozenset(payload["capabilities"])
    allowed_imports = frozenset(payload["allowed_imports"])
    allowed_commands = frozenset(payload["allowed_commands"])
    allowed_hosts = frozenset(payload["allowed_network_hosts"])
    limits: WorkspaceResourceLimits = payload["limits"]
    root = Path(str(payload["workspace_root"])).resolve()
    safe_builtins = {
        name: getattr(builtins, name)
        for name in dir(builtins)
        if not name.startswith("_") and name not in _BLOCKED_NAMES and name != "open"
    }

    def safe_import(name: str, globals: Any = None, locals: Any = None, fromlist: Any = (), level: int = 0) -> Any:
        del globals, locals, fromlist
        top_level = name.split(".", 1)[0]
        if level or "package_import" not in capabilities or top_level not in allowed_imports or top_level in _BLOCKED_MODULES:
            raise PermissionError(f"import denied: {name}")
        return builtins.__import__(name)

    def resolve_path(file_path: str, capability: str) -> Path:
        if capability not in capabilities:
            raise PermissionError(f"{capability} capability was not granted")
        candidate = (root / file_path).resolve()
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise PermissionError(f"path escapes workspace root: {file_path}") from exc
        return candidate

    def read_bytes(file_path: str) -> bytes:
        data = resolve_path(file_path, "workspace_read").read_bytes()
        if len(data) > limits.max_file_bytes:
            raise ValueError("file exceeds workspace byte limit")
        return data

    def write_bytes(file_path: str, content: bytes) -> None:
        if len(content) > limits.max_file_bytes:
            raise ValueError("file exceeds workspace byte limit")
        path = resolve_path(file_path, "workspace_write")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

    def run_command(argv: Iterable[str], timeout_seconds: float | None = None) -> dict[str, Any]:
        if "subprocess" not in capabilities:
            raise PermissionError("subprocess capability was not granted")
        args = [str(item) for item in argv]
        if not args or args[0] not in allowed_commands:
            raise PermissionError(f"command denied: {args[0] if args else ''}")
        timeout = min(timeout_seconds or limits.subprocess_timeout_seconds, limits.subprocess_timeout_seconds)
        completed = subprocess.run(  # noqa: S603
            args,
            cwd=root,
            env={"PATH": os.environ.get("PATH", ""), "LANG": "C.UTF-8"},
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return {"stdout": completed.stdout, "stderr": completed.stderr, "exit_code": completed.returncode}

    def network_fetch(url: str) -> bytes:
        if "network" not in capabilities:
            raise PermissionError("network capability was not granted")
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme != "https" or not parsed.hostname or parsed.hostname not in allowed_hosts:
            raise PermissionError(f"network host denied: {parsed.hostname or ''}")
        with urllib.request.urlopen(url, timeout=limits.timeout_seconds) as response:  # noqa: S310
            data = bytes(response.read(limits.max_network_bytes + 1))
        if len(data) > limits.max_network_bytes:
            raise ValueError("network response exceeds byte limit")
        return data

    safe_builtins["__import__"] = safe_import
    namespace: dict[str, Any] = {
        "__name__": "__autocontext_research__",
        "__builtins__": safe_builtins,
        "answer": {"content": "", "ready": False},
        **_copy_plain_mapping(payload.get("variables", {})),
    }
    if "workspace_read" in capabilities:
        namespace["workspace_read_bytes"] = read_bytes
        namespace["workspace_read_text"] = lambda path: read_bytes(path).decode("utf-8")
    if "workspace_write" in capabilities:
        namespace["workspace_write_bytes"] = write_bytes
        namespace["workspace_write_text"] = lambda path, content: write_bytes(path, str(content).encode("utf-8"))
    if "subprocess" in capabilities:
        namespace["run_subprocess"] = run_command
    if "network" in capabilities:
        namespace["network_fetch"] = network_fetch
    for source in payload.get("helper_sources", ()):
        exec(compile(source, "<workspace-helper>", "exec"), namespace, namespace)  # noqa: S102

    stdout = io.StringIO()
    error: str | None = None
    try:
        module = ast.parse(str(payload["code"]), mode="exec")
        body = list(module.body)
        trailing = body.pop() if body and isinstance(body[-1], ast.Expr) else None
        with contextlib.redirect_stdout(stdout):
            if body:
                exec(compile(ast.Module(body=body, type_ignores=[]), "<research-workspace>", "exec"), namespace, namespace)  # noqa: S102
            if isinstance(trailing, ast.Expr):
                value = eval(compile(ast.Expression(trailing.value), "<research-workspace>", "eval"), namespace, namespace)  # noqa: S307
                if value is not None:
                    print(repr(value))
    except BaseException:  # noqa: BLE001
        error = traceback.format_exc()
    rendered_stdout = stdout.getvalue()
    if len(rendered_stdout) > limits.max_stdout_chars:
        rendered_stdout = rendered_stdout[: limits.max_stdout_chars] + "\n... [truncated]"
    variables, skipped = _export_namespace(namespace)
    return {
        "stdout": rendered_stdout,
        "error": error,
        "answer": dict(namespace.get("answer", {})),
        "variables": variables,
        "skipped": skipped,
        "host_calls": (),
        "stdout_chars": len(rendered_stdout),
        "variable_count": len(variables),
    }


def _validate_capable_code(code: str, allowed_imports: frozenset[str]) -> None:
    try:
        module = ast.parse(code, mode="exec")
    except SyntaxError:
        return
    for node in ast.walk(module):
        if isinstance(node, ast.Attribute) and node.attr.startswith("__"):
            raise PermissionError(f"dunder attribute access denied: {node.attr}")
        if isinstance(node, ast.Name) and (node.id in _BLOCKED_NAMES or node.id in _BLOCKED_MODULES):
            raise PermissionError(f"name denied: {node.id}")
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [alias.name for alias in node.names] if isinstance(node, ast.Import) else [node.module or ""]
            for name in names:
                top_level = name.split(".", 1)[0]
                if top_level not in allowed_imports or top_level in _BLOCKED_MODULES:
                    raise PermissionError(f"import denied: {name}")


def _new_helper_sources(code: str, existing: Iterable[str]) -> list[str]:
    module = ast.parse(code, mode="exec")
    known = set(existing)
    sources: list[str] = []
    for node in module.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            source = ast.unparse(node)
            if source not in known:
                sources.append(source)
    return sources


def _copy_plain_mapping(source: Mapping[str, Any]) -> dict[str, Any]:
    copied: dict[str, Any] = {}
    for name, value in source.items():
        plain = _copy_plain(value)
        if plain is not _SKIP:
            copied[str(name)] = plain
    return copied


_SKIP = object()


def _copy_plain(value: Any, depth: int = 0) -> Any:
    if depth > 20:
        return _SKIP
    if type(value) in {type(None), bool, int, float, str, bytes}:
        return value
    if type(value) is list:
        copied = [_copy_plain(item, depth + 1) for item in value]
        return copied if all(item is not _SKIP for item in copied) else _SKIP
    if type(value) is tuple:
        copied = [_copy_plain(item, depth + 1) for item in value]
        return tuple(copied) if all(item is not _SKIP for item in copied) else _SKIP
    if type(value) is dict:
        copied_dict: dict[Any, Any] = {}
        for key, item in value.items():
            copied_key = _copy_plain(key, depth + 1)
            copied_item = _copy_plain(item, depth + 1)
            if copied_key is _SKIP or copied_item is _SKIP:
                return _SKIP
            copied_dict[copied_key] = copied_item
        return copied_dict
    if type(value) in {set, frozenset}:
        copied = [_copy_plain(item, depth + 1) for item in value]
        if any(item is _SKIP for item in copied):
            return _SKIP
        return type(value)(copied)
    return _SKIP


def _export_namespace(namespace: Mapping[str, Any]) -> tuple[dict[str, Any], tuple[str, ...]]:
    variables: dict[str, Any] = {}
    skipped: list[str] = []
    for name, value in namespace.items():
        if name in _INFRA_NAMES or name.startswith("_") or callable(value) or isinstance(value, type(os)):
            continue
        copied = _copy_plain(value)
        if copied is _SKIP:
            skipped.append(name)
        else:
            variables[name] = copied
    return variables, tuple(sorted(skipped))


def _snapshot_files(root: Path, max_file_bytes: int) -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"workspace snapshots do not follow symbolic links: {path.relative_to(root)}")
        if path.is_file():
            data = path.read_bytes()
            if len(data) > max_file_bytes:
                raise ValueError(f"workspace file exceeds byte limit: {path.relative_to(root)}")
            files[path.relative_to(root).as_posix()] = data
    return files


def _restore_files(root: Path, files: Mapping[str, bytes], max_file_bytes: int) -> None:
    staging = Path(tempfile.mkdtemp(prefix=".autocontext-restore-", dir=root.parent)).resolve()
    try:
        for relative, data in files.items():
            if len(data) > max_file_bytes:
                raise ValueError(f"snapshot file exceeds byte limit: {relative}")
            target = (staging / relative).resolve()
            try:
                target.relative_to(staging)
            except ValueError as exc:
                raise ValueError(f"snapshot path escapes workspace root: {relative}") from exc
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        _replace_workspace(staging, root, max_file_bytes)
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def _copy_workspace(source: Path, destination: Path, max_file_bytes: int) -> None:
    for relative, data in _snapshot_files(source, max_file_bytes).items():
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)


def _replace_workspace(source: Path, destination: Path, max_file_bytes: int) -> None:
    files = _snapshot_files(source, max_file_bytes)
    for child in destination.iterdir():
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child)
        else:
            child.unlink()
    for relative, data in files.items():
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)


def _resource_detail(response: Mapping[str, Any]) -> str:
    return f"stdout_chars={response.get('stdout_chars', 0)} variables={response.get('variable_count', 0)}"


def _safe_id(value: str) -> str:
    cleaned = "".join(char if char.isalnum() or char in {"-", "_"} else "-" for char in value)
    return cleaned[:48] or "workspace"


__all__ = [
    "ResearchWorkspace",
    "ResearchWorkspaceBenchmark",
    "ResearchWorkspaceSnapshot",
    "WorkspaceAuditEvent",
    "WorkspaceCapability",
    "WorkspaceCapabilityRequest",
    "WorkspaceCleanupResult",
    "WorkspaceGrant",
    "WorkspaceLifecyclePolicy",
    "WorkspaceProfile",
    "WorkspaceResourceLimits",
    "benchmark_research_workspace",
    "grant_workspace_access",
]
