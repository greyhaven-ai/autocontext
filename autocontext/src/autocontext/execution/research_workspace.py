"""Capability-scoped persistent research workspaces (AC-977).

The existing :class:`InterpreterWorkspace` remains the default restricted
scratch surface. This module orchestrates explicitly approved, process-backed
workspaces while execution and filesystem boundaries live in focused helpers.
"""

from __future__ import annotations

import ast
import shutil
import sys
import tempfile
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal

from autocontext.execution.interpreter_workspace import InterpreterWorkspace, WorkspaceVariable
from autocontext.execution.research_workspace_files import (
    copy_workspace,
    replace_workspace,
    restore_files,
    safe_workspace_id,
    snapshot_files,
)
from autocontext.execution.research_workspace_models import (
    CapabilityApprover,
    HostBridge,
    ResearchWorkspaceBenchmark,
    ResearchWorkspaceSnapshot,
    WorkspaceAuditEvent,
    WorkspaceCapability,
    WorkspaceCapabilityRequest,
    WorkspaceCleanupResult,
    WorkspaceGrant,
    WorkspaceLifecyclePolicy,
    WorkspaceProfile,
    WorkspaceResourceLimits,
)
from autocontext.execution.research_workspace_runtime import (
    copy_plain_mapping,
    new_helper_sources,
    resource_detail,
    run_in_child,
    validate_capable_code,
)
from autocontext.harness.repl.types import ReplResult
from autocontext.runtimes.workspace_env import RuntimeWorkspaceEnv, create_local_workspace_env


def grant_workspace_access(
    request: WorkspaceCapabilityRequest,
    approver: CapabilityApprover | None = None,
) -> WorkspaceGrant:
    """Resolve an explicit request. Elevated capabilities default to deny."""

    requested = request.requested_capabilities
    if request.profile == "restricted_scratch":
        granted: frozenset[WorkspaceCapability] = frozenset()
    elif approver is not None and approver(request):
        # Process isolation is not an OS security boundary. Until the isolated
        # profile is backed by a real sandbox, an allow-listed module,
        # interpreter, or shell can expose already-loaded file/network/process
        # primitives and escape every workspace-level grant.
        unsafe_without_os_sandbox = {"package_import", "subprocess"}
        granted = requested - unsafe_without_os_sandbox if request.profile == "isolated_sandbox" else requested
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
    """Run a deterministic multi-generation code/research acceptance task."""

    restricted = ResearchWorkspace(WorkspaceCapabilityRequest(workspace_id="benchmark-restricted", lifecycle="delete_on_close"))
    restricted_start = time.perf_counter()
    restricted_result = restricted.run("workspace_read_text('observations.txt')")
    restricted_wall = time.perf_counter() - restricted_start
    restricted_cleanup = restricted.close().outcome

    request = WorkspaceCapabilityRequest(
        workspace_id="benchmark-capable",
        profile="trusted_local",
        requested_capabilities=frozenset({"workspace_read", "workspace_write", "package_import", "subprocess"}),
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
        f"check = run_subprocess([{sys.executable!r}, '-c', 'print(6 * 7)'])\ncheck_value = int(check['stdout'])"
    )
    prompt_chars.append(len(capable.render_markdown()))
    generation_three = capable.run("workspace_write_text('report.txt', f'mean={observed_mean}; check={check_value}')")
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
            if request.profile == "isolated_sandbox" and self.grant.denied_capabilities & {
                "package_import",
                "subprocess",
            }:
                raise PermissionError(
                    f"isolated_sandbox capabilities unavailable until an OS sandbox backend is configured: {denied}"
                )
            raise PermissionError(f"workspace capabilities were not approved: {denied}")
        self._owned_root = workspace_root is None
        self._root = (
            Path(tempfile.mkdtemp(prefix=f"autocontext-{safe_workspace_id(request.workspace_id)}-"))
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
        self._variables: dict[str, Any] = copy_plain_mapping(seed or {})
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
            validate_capable_code(code, self.request.allowed_imports)
        except PermissionError as exc:
            self._record("execute", "denied", detail=str(exc))
            return ReplResult(stdout="", error=f"PermissionError: {exc}", answer={})
        staging = Path(tempfile.mkdtemp(prefix=".autocontext-stage-", dir=self._root.parent)).resolve()
        try:
            copy_workspace(self._root, staging, self.request.limits.max_file_bytes)
            response = run_in_child(
                {
                    "code": code,
                    "variables": self._variables,
                    "helper_sources": tuple(self._helper_sources),
                    "workspace_root": str(staging),
                    "profile": self.request.profile,
                    "capabilities": tuple(self.grant.granted_capabilities),
                    "allowed_imports": tuple(self.request.allowed_imports),
                    "allowed_commands": tuple(self.request.allowed_commands),
                    "allowed_network_hosts": tuple(self.request.allowed_network_hosts),
                    "limits": self.request.limits,
                },
                self.request.limits.timeout_seconds,
            )
            if response is None:
                self._record("execute", "timeout", detail="child process terminated")
                return ReplResult(stdout="", error="CodeTimeout: child process terminated", answer={})
            result = ReplResult(
                stdout=str(response.get("stdout", "")),
                error=str(response["error"]) if response.get("error") else None,
                answer=dict(response.get("answer", {})),
            )
            if result.error is None:
                next_variables = copy_plain_mapping(response.get("variables", {}))
                next_helper_sources = [
                    *self._helper_sources,
                    *new_helper_sources(code, self._helper_sources),
                ]
                try:
                    replace_workspace(staging, self._root, self.request.limits.max_file_bytes)
                except (OSError, ValueError) as exc:
                    detail = f"{type(exc).__name__}: {exc}"
                    self._record("execute", "commit_error", detail=detail[-240:])
                    return ReplResult(stdout=result.stdout, error=f"WorkspaceCommitError: {detail}", answer={})
                self._variables = next_variables
                self._helper_sources = next_helper_sources
                self._record("execute", "success", detail=resource_detail(response))
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
            variables = copy_plain_mapping(self._variables)
            skipped = ()
        snapshot = ResearchWorkspaceSnapshot(
            workspace_id=self.request.workspace_id,
            variables=variables,
            helper_sources=tuple(self._helper_sources),
            files=snapshot_files(self._root, self.request.limits.max_file_bytes),
            skipped_variables=skipped,
        )
        self._record("snapshot", "success", detail=f"files={len(snapshot.files)}")
        return snapshot

    def restore(self, snapshot: ResearchWorkspaceSnapshot) -> None:
        self._ensure_open()
        if snapshot.workspace_id != self.request.workspace_id:
            raise ValueError("snapshot belongs to a different workspace")
        next_variables = copy_plain_mapping(snapshot.variables)
        next_helper_sources = list(snapshot.helper_sources)
        restore_files(self._root, snapshot.files, self.request.limits.max_file_bytes)
        if self._restricted is not None:
            from autocontext.execution.interpreter_workspace import WorkspaceSnapshot

            self._restricted.restore(WorkspaceSnapshot(variables=next_variables))
        else:
            self._variables = next_variables
            self._helper_sources = next_helper_sources
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
