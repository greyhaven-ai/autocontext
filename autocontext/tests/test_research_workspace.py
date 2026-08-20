from __future__ import annotations

import sys
from pathlib import Path

import pytest

from autocontext.execution import research_workspace_runtime
from autocontext.execution.research_workspace import (
    ResearchWorkspace,
    WorkspaceCapabilityRequest,
    WorkspaceResourceLimits,
    benchmark_research_workspace,
)


def _approve(_: WorkspaceCapabilityRequest) -> bool:
    return True


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


def test_isolated_subprocess_fails_closed_but_trusted_local_can_run_approved_command(tmp_path: Path) -> None:
    isolated_request = WorkspaceCapabilityRequest(
        workspace_id="isolated-process",
        profile="isolated_sandbox",
        requested_capabilities=frozenset({"subprocess"}),
        allowed_commands=frozenset({sys.executable}),
    )
    with pytest.raises(PermissionError, match="unavailable until an OS sandbox"):
        ResearchWorkspace(isolated_request, workspace_root=tmp_path / "isolated", approver=_approve)

    isolated_import_request = WorkspaceCapabilityRequest(
        workspace_id="isolated-import",
        profile="isolated_sandbox",
        requested_capabilities=frozenset({"package_import"}),
        allowed_imports=frozenset({"statistics"}),
    )
    with pytest.raises(PermissionError, match="package_import"):
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
    workspace = ResearchWorkspace(request, workspace_root=tmp_path, approver=_approve)

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
    workspace = ResearchWorkspace(request, workspace_root=tmp_path, approver=_approve, seed={"counter": 1})

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
    workspace = ResearchWorkspace(request, workspace_root=tmp_path, approver=_approve, seed={"counter": 1})
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
