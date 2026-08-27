from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

import pytest

from autocontext.execution import research_workspace_runtime
from autocontext.execution.research_workspace import (
    ResearchWorkspace,
    WorkspaceCapabilityRequest,
    WorkspaceResourceLimits,
    benchmark_research_workspace,
)
from autocontext.execution.research_workspace_files import restore_files, snapshot_files
from autocontext.execution.research_workspace_models import (
    ResearchSandboxExecutionRequest,
    ResearchSandboxExecutionResult,
    SandboxBackendCapabilities,
    SandboxBackendCleanupResult,
    WorkspaceSecretGrant,
)
from autocontext.execution.scenario_remote_package import DEFAULT_REMOTE_RUNTIME_IMAGE


def _approve(_: WorkspaceCapabilityRequest) -> bool:
    return True


class _TestSandboxBackend:
    """Exercise the backend boundary using the legacy child kernel in tests only."""

    def __init__(self, *, capabilities: SandboxBackendCapabilities | None = None) -> None:
        self._capabilities = capabilities or SandboxBackendCapabilities(
            backend_name="test-only-emulator",
            os_isolation=True,
            workspace_mounts=True,
            network_policy=True,
            process_limits=True,
            environment_scrubbing=True,
            secret_grants=True,
            transactional_files=True,
            terminable_execution=True,
            cleanup_verification=True,
        )
        self.requests: list[ResearchSandboxExecutionRequest] = []
        self.cleaned: list[str] = []

    def capabilities(self) -> SandboxBackendCapabilities:
        return self._capabilities

    def execute(self, request: ResearchSandboxExecutionRequest) -> ResearchSandboxExecutionResult:
        self.requests.append(request)
        with tempfile.TemporaryDirectory(prefix="autocontext-test-sandbox-") as directory:
            root = Path(directory)
            restore_files(root, request.files, request.limits.max_file_bytes)
            response = research_workspace_runtime.run_in_child(
                {
                    "code": request.code,
                    "variables": request.variables,
                    "helper_sources": request.helper_sources,
                    "workspace_root": str(root),
                    "profile": "trusted_local",
                    "capabilities": tuple(request.granted_capabilities),
                    "allowed_imports": tuple(request.allowed_imports),
                    "allowed_commands": tuple(request.allowed_commands),
                    "allowed_network_hosts": tuple(request.allowed_network_hosts),
                    "limits": request.limits,
                },
                request.limits.timeout_seconds,
            )
            if response is None:
                raise TimeoutError("test sandbox timed out")
            helpers = request.helper_sources
            if not response.get("error"):
                helpers = (*helpers, *research_workspace_runtime.new_helper_sources(request.code, helpers))
            return ResearchSandboxExecutionResult(
                stdout=str(response.get("stdout", "")),
                error=str(response["error"]) if response.get("error") else None,
                answer=dict(response.get("answer", {})),
                variables=dict(response.get("variables", {})),
                helper_sources=helpers,
                files=snapshot_files(root, request.limits.max_file_bytes),
                session_id="test-session",
                detail="test sandbox",
            )

    def cleanup(self, workspace_id: str) -> SandboxBackendCleanupResult:
        self.cleaned.append(workspace_id)
        return SandboxBackendCleanupResult(succeeded=True)


class _LeakingSandboxBackend(_TestSandboxBackend):
    def __init__(self, leaked_value: str) -> None:
        super().__init__()
        self._leaked_value = leaked_value

    def execute(self, request: ResearchSandboxExecutionRequest) -> ResearchSandboxExecutionResult:
        self.requests.append(request)
        return ResearchSandboxExecutionResult(stdout=self._leaked_value)


def test_restricted_scratch_remains_default_and_rejects_elevation(tmp_path: Path) -> None:
    workspace = ResearchWorkspace(WorkspaceCapabilityRequest(workspace_id="restricted"), workspace_root=tmp_path)

    result = workspace.run("value = 4\nvalue * 2")

    assert result.stdout == "8"
    assert workspace.grant.granted_capabilities == frozenset()
    assert workspace.request.profile == "restricted_scratch"
    assert "profile: restricted_scratch" in workspace.render_markdown()
    workspace.close()

    with pytest.raises(ValueError, match="does not accept elevated"):
        WorkspaceCapabilityRequest(
            workspace_id="bad",
            requested_capabilities=frozenset({"workspace_read"}),
        )


def test_capable_profile_requires_explicit_approval(tmp_path: Path) -> None:
    request = WorkspaceCapabilityRequest(
        workspace_id="approval",
        profile="isolated_sandbox",
        requested_capabilities=frozenset({"workspace_read"}),
    )

    with pytest.raises(PermissionError, match="not approved"):
        ResearchWorkspace(request, workspace_root=tmp_path)


def test_isolated_profile_requires_backend_and_all_security_controls(tmp_path: Path) -> None:
    request = WorkspaceCapabilityRequest(
        workspace_id="isolation",
        profile="isolated_sandbox",
        requested_capabilities=frozenset({"workspace_read"}),
    )

    with pytest.raises(PermissionError, match="requires an OS sandbox backend"):
        ResearchWorkspace(request, workspace_root=tmp_path / "missing", approver=_approve)

    weak_backend = _TestSandboxBackend(capabilities=SandboxBackendCapabilities(backend_name="weak"))
    with pytest.raises(PermissionError, match="lacks required controls"):
        ResearchWorkspace(
            request,
            workspace_root=tmp_path / "weak",
            approver=_approve,
            sandbox_backend=weak_backend,
        )


def test_docker_backend_declares_hardened_deny_network_command(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from autocontext.execution.docker_research_sandbox import DockerResearchSandboxBackend

    monkeypatch.setattr(shutil, "which", lambda _: "/usr/bin/docker")
    backend = DockerResearchSandboxBackend()
    command = backend._docker_command(  # noqa: SLF001 - verify the security-critical adapter command
        "sandbox-test",
        "workspace-test",
        tmp_path / "input",
        tmp_path / "output",
        tmp_path / "workspace",
        None,
    )

    assert "--read-only" in command
    assert command[command.index("--network") + 1] == "none"
    assert command[command.index("--cap-drop") + 1] == "ALL"
    assert "no-new-privileges" in command
    assert "--pids-limit" in command
    assert "--memory" in command
    assert "--cpus" in command
    assert DEFAULT_REMOTE_RUNTIME_IMAGE in command
    assert not any("/opt/autocontext-src" in argument for argument in command)
    assert {argument.rsplit("dst=", 1)[-1].split(",", 1)[0] for argument in command if "dst=" in argument} == {
        "/input",
        "/output",
        "/workspace",
    }

    request = ResearchSandboxExecutionRequest(
        workspace_id="network-denied",
        sequence=1,
        code="pass",
        variables={},
        helper_sources=(),
        files={},
        granted_capabilities=frozenset({"network"}),
        allowed_imports=frozenset(),
        allowed_commands=frozenset(),
        allowed_network_hosts=frozenset({"example.com"}),
        secret_grants=(),
        limits=WorkspaceResourceLimits(),
    )
    with pytest.raises(PermissionError, match="deny-network only"):
        backend.execute(request)


@pytest.mark.skipif(
    os.environ.get("AUTOCONTEXT_RUN_DOCKER_TESTS") != "1" or shutil.which("docker") is None,
    reason="set AUTOCONTEXT_RUN_DOCKER_TESTS=1 on a Docker-capable CI worker",
)
def test_real_docker_workspace_is_transactional_and_cannot_read_unmounted_host(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from autocontext.execution.docker_research_sandbox import DockerResearchSandboxBackend

    host_secret = tmp_path / "host-only.txt"
    host_secret.write_text("must-not-enter-container", encoding="utf-8")
    monkeypatch.setenv("AUTOCONTEXT_HOST_SENTINEL", "must-not-enter-container")
    backend = DockerResearchSandboxBackend()
    request = WorkspaceCapabilityRequest(
        workspace_id="docker-live",
        profile="isolated_sandbox",
        requested_capabilities=frozenset({"workspace_read", "workspace_write", "subprocess"}),
        allowed_commands=frozenset({"/usr/local/bin/python"}),
        limits=WorkspaceResourceLimits(timeout_seconds=10.0),
        lifecycle="delete_on_close",
    )
    workspace = ResearchWorkspace(request, approver=_approve, sandbox_backend=backend)
    host_probe_source = f"from pathlib import Path; print(Path({str(host_secret)!r}).read_text())"
    denied = workspace.run(f"probe = run_subprocess(['/usr/local/bin/python', '-c', {host_probe_source!r}])")
    probes = workspace.run(
        "network_probe = run_subprocess(['/usr/local/bin/python', '-c', "
        "'import socket; socket.create_connection((\"1.1.1.1\", 53), 0.2)'])\n"
        "env_probe = run_subprocess(['/usr/local/bin/python', '-c', "
        '\'import os; print(os.environ.get("AUTOCONTEXT_HOST_SENTINEL", ""))\'])\n'
        "hosts_probe = run_subprocess(['/usr/local/bin/python', '-c', "
        "'print(open(\"/etc/hosts\").read())'])"
    )
    failed = workspace.run("workspace_write_text('candidate.txt', 'not committed')\nraise RuntimeError('reject')")
    succeeded = workspace.run("workspace_write_text('accepted.txt', 'committed')")

    assert denied.error is None
    assert probes.error is None
    assert denied.stdout == ""
    assert workspace.run("probe['exit_code']").stdout.strip() != "0"
    assert workspace.run("network_probe['exit_code']").stdout.strip() != "0"
    assert workspace.run("env_probe['stdout'].strip()").stdout.strip() == "''"
    assert "must-not-enter-container" not in workspace.run("hosts_probe['stdout']").stdout
    assert failed.error is not None
    assert not workspace.runtime_env.exists("candidate.txt")
    assert succeeded.error is None
    assert workspace.runtime_env.read_file("accepted.txt") == "committed"
    assert workspace.close().outcome == "deleted"


def test_isolated_secret_grants_are_opaque_and_leaks_are_rejected(tmp_path: Path) -> None:
    grant = WorkspaceSecretGrant(
        name="papers-api",
        grant_id="opaque-grant-123",
        expires_at=time.time() + 60,
        env_var="PAPERS_API_TOKEN",
    )
    request = WorkspaceCapabilityRequest(
        workspace_id="secret",
        profile="isolated_sandbox",
        secret_grants=(grant,),
    )
    backend = _LeakingSandboxBackend(grant.grant_id)
    workspace = ResearchWorkspace(
        request,
        workspace_root=tmp_path,
        approver=_approve,
        sandbox_backend=backend,
    )

    result = workspace.run("answer['ready'] = True")

    assert result.stdout == ""
    assert result.error and result.error.startswith("SandboxSecurityError")
    assert grant.grant_id not in result.error
    assert backend.requests[0].secret_grants == (grant,)
    cleanup = workspace.close()
    assert cleanup.outcome == "retained"
    assert backend.cleaned == ["secret"]


def test_trusted_local_workspace_persists_helpers_files_and_imports(tmp_path: Path) -> None:
    request = WorkspaceCapabilityRequest(
        workspace_id="research",
        profile="trusted_local",
        requested_capabilities=frozenset({"workspace_read", "workspace_write", "package_import"}),
        allowed_imports=frozenset({"statistics"}),
    )
    workspace = ResearchWorkspace(request, workspace_root=tmp_path, approver=_approve, seed={"samples": [1, 3, 8]})

    first = workspace.run(
        "import statistics\n"
        "def summarize(values):\n"
        "    return statistics.mean(values)\n"
        "workspace_write_text('notes/result.txt', str(summarize(samples)))\n"
        "mean = summarize(samples)"
    )
    second = workspace.run("workspace_read_text('notes/result.txt'), mean")

    assert first.error is None
    assert second.error is None
    assert "('4', 4)" in second.stdout
    assert (tmp_path / "notes" / "result.txt").read_text() == "4"
    assert {item.name for item in workspace.variables()} >= {"samples", "mean", "summarize"}
    events = workspace.audit_events
    assert events[0].profile == "trusted_local"
    assert set(events[-1].capabilities) >= {"workspace_read", "workspace_write", "package_import"}
    workspace.close()


def test_isolated_subprocess_requires_backend_but_trusted_local_can_run_approved_command(tmp_path: Path) -> None:
    isolated_request = WorkspaceCapabilityRequest(
        workspace_id="isolated-process",
        profile="isolated_sandbox",
        requested_capabilities=frozenset({"subprocess"}),
        allowed_commands=frozenset({sys.executable}),
    )
    with pytest.raises(PermissionError, match="requires an OS sandbox backend"):
        ResearchWorkspace(isolated_request, workspace_root=tmp_path / "isolated", approver=_approve)

    isolated_import_request = WorkspaceCapabilityRequest(
        workspace_id="isolated-import",
        profile="isolated_sandbox",
        requested_capabilities=frozenset({"package_import"}),
        allowed_imports=frozenset({"statistics"}),
    )
    with pytest.raises(PermissionError, match="requires an OS sandbox backend"):
        ResearchWorkspace(isolated_import_request, workspace_root=tmp_path / "isolated-import", approver=_approve)

    trusted_request = WorkspaceCapabilityRequest(
        workspace_id="trusted-process",
        profile="trusted_local",
        requested_capabilities=frozenset({"subprocess"}),
        allowed_commands=frozenset({sys.executable}),
    )
    trusted = ResearchWorkspace(trusted_request, workspace_root=tmp_path / "trusted", approver=_approve)
    result = trusted.run(f"run_subprocess([{sys.executable!r}, '-c', 'print(21 * 2)'])['stdout'].strip()")

    assert result.error is None
    assert result.stdout.strip() == "'42'"
    trusted.close()


def test_path_network_and_secret_access_fail_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTOCONTEXT_TEST_SECRET", "do-not-expose")
    request = WorkspaceCapabilityRequest(
        workspace_id="denials",
        profile="isolated_sandbox",
        requested_capabilities=frozenset({"workspace_read", "workspace_write"}),
    )
    workspace = ResearchWorkspace(
        request,
        workspace_root=tmp_path,
        approver=_approve,
        sandbox_backend=_TestSandboxBackend(),
    )

    escaped = workspace.run("workspace_write_text('../outside.txt', 'bad')")
    network = workspace.run("network_fetch('https://example.com')")
    secret = workspace.run("import os\nos.environ['AUTOCONTEXT_TEST_SECRET']")

    assert "path escapes workspace root" in (escaped.error or "")
    assert "network_fetch" in (network.error or "")
    assert "import denied: os" in (secret.error or "")
    assert not (tmp_path.parent / "outside.txt").exists()
    workspace.close()


def test_network_fetch_denies_redirects_before_opening_the_target(monkeypatch: pytest.MonkeyPatch) -> None:
    redirected_to: list[str] = []

    class RedirectingOpener:
        def __init__(self, handler: research_workspace_runtime._DenyRedirectHandler) -> None:
            self.handler = handler

        def open(self, url: str, *, timeout: float) -> object:
            del timeout
            redirected_to.append("https://internal.example/secrets")
            return self.handler.redirect_request(
                research_workspace_runtime.urllib.request.Request(url),
                None,
                302,
                "Found",
                {},
                redirected_to[-1],
            )

    def build_opener(handler: research_workspace_runtime._DenyRedirectHandler) -> RedirectingOpener:
        assert isinstance(handler, research_workspace_runtime._DenyRedirectHandler)
        return RedirectingOpener(handler)

    monkeypatch.setattr(research_workspace_runtime, "require_online", lambda _: None)
    monkeypatch.setattr(research_workspace_runtime.urllib.request, "build_opener", build_opener)

    with pytest.raises(PermissionError, match="redirects are denied"):
        research_workspace_runtime._fetch_network_bytes(
            "https://allowed.example/start",
            allowed_hosts=frozenset({"allowed.example"}),
            limits=WorkspaceResourceLimits(),
        )
    assert redirected_to == ["https://internal.example/secrets"]


def test_timeout_discards_memory_and_file_mutation(tmp_path: Path) -> None:
    request = WorkspaceCapabilityRequest(
        workspace_id="timeout",
        profile="isolated_sandbox",
        requested_capabilities=frozenset({"workspace_read", "workspace_write"}),
        limits=WorkspaceResourceLimits(timeout_seconds=0.35),
    )
    workspace = ResearchWorkspace(
        request,
        workspace_root=tmp_path,
        approver=_approve,
        sandbox_backend=_TestSandboxBackend(),
        seed={"counter": 1},
    )

    result = workspace.run("counter = 999\nworkspace_write_text('late.txt', 'bad')\nwhile True:\n    pass")
    probe = workspace.run("counter")

    assert result.error and result.error.startswith("CodeTimeout")
    assert probe.stdout.strip() == "1"
    assert not (tmp_path / "late.txt").exists()
    assert any(event.outcome == "timeout" for event in workspace.audit_events)
    workspace.close()


def test_failed_file_activation_preserves_files_variables_and_helpers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = WorkspaceCapabilityRequest(
        workspace_id="transaction",
        profile="isolated_sandbox",
        requested_capabilities=frozenset({"workspace_read", "workspace_write"}),
    )
    workspace = ResearchWorkspace(
        request,
        workspace_root=tmp_path,
        approver=_approve,
        sandbox_backend=_TestSandboxBackend(),
        seed={"counter": 1},
    )
    baseline = workspace.run("def keep():\n    return counter\nworkspace_write_text('state.txt', 'old')")
    assert baseline.error is None

    original_replace = Path.replace

    def fail_candidate_activation(path: Path, target: str | Path) -> Path:
        if path.name.startswith(".autocontext-commit-") and Path(target) == tmp_path:
            raise OSError("simulated activation failure")
        return original_replace(path, target)

    with monkeypatch.context() as scoped:
        scoped.setattr(Path, "replace", fail_candidate_activation)
        failed = workspace.run(
            "counter = 2\n"
            "def discard():\n"
            "    return counter\n"
            "workspace_write_text('state.txt', 'new')\n"
            "workspace_write_text('new.txt', 'uncommitted')"
        )

    assert failed.error and failed.error.startswith("WorkspaceCommitError: OSError")
    assert (tmp_path / "state.txt").read_text() == "old"
    assert not (tmp_path / "new.txt").exists()
    assert workspace.run("counter, keep()").stdout.strip() == "(1, 1)"
    assert {item.name for item in workspace.variables()} >= {"counter", "keep"}
    assert "discard" not in {item.name for item in workspace.variables()}
    assert any(event.outcome == "commit_error" for event in workspace.audit_events)
    workspace.close()


def test_snapshot_restore_and_owned_teardown_are_explicit() -> None:
    request = WorkspaceCapabilityRequest(
        workspace_id="lifecycle",
        profile="trusted_local",
        requested_capabilities=frozenset({"workspace_read", "workspace_write"}),
        lifecycle="delete_on_close",
    )
    workspace = ResearchWorkspace(request, approver=_approve, seed={"version": 1})
    root = workspace.workspace_root
    assert workspace.run("workspace_write_text('state.txt', 'one')").error is None
    snapshot = workspace.snapshot()
    assert workspace.run("version = 2\nworkspace_write_text('state.txt', 'two')").error is None

    workspace.restore(snapshot)

    assert workspace.run("version").stdout.strip() == "1"
    assert (root / "state.txt").read_text() == "one"
    cleanup = workspace.close()
    assert cleanup.outcome == "deleted"
    assert not root.exists()
    assert workspace.close().outcome == "already_closed"


def test_host_bridge_is_typed_and_separately_gated(tmp_path: Path) -> None:
    calls: list[tuple[str, dict[str, object]]] = []

    def bridge(name: str, arguments: dict[str, object]) -> object:
        calls.append((name, arguments))
        return {"ok": True}

    request = WorkspaceCapabilityRequest(
        workspace_id="host",
        profile="trusted_local",
        requested_capabilities=frozenset({"host_bridge"}),
    )
    workspace = ResearchWorkspace(request, workspace_root=tmp_path, approver=_approve, host_bridge=bridge)

    assert workspace.host_call("request_credentialed_fetch", {"resource": "paper-1"}) == {"ok": True}
    assert calls == [("request_credentialed_fetch", {"resource": "paper-1"})]
    assert workspace.audit_events[-1].resource == "request_credentialed_fetch"
    workspace.close()


def test_code_research_benchmark_improves_quality_with_flat_context_and_cleanup() -> None:
    result = benchmark_research_workspace()

    assert result.restricted_task_quality == 0.0
    assert result.capable_task_quality == 1.0
    assert result.restricted_wall_seconds >= 0
    assert result.capable_wall_seconds > 0
    assert max(result.capable_prompt_chars) - min(result.capable_prompt_chars) < 500
    assert result.restricted_cleanup == "deleted"
    assert result.capable_cleanup == "deleted"
