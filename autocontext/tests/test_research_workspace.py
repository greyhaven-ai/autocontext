from __future__ import annotations

import sys
from pathlib import Path

import pytest

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


def test_isolated_workspace_persists_helpers_files_imports_and_subprocess(tmp_path: Path) -> None:
    request = WorkspaceCapabilityRequest(
        workspace_id="research",
        profile="isolated_sandbox",
        requested_capabilities=frozenset(
            {"workspace_read", "workspace_write", "package_import", "subprocess"}
        ),
        allowed_imports=frozenset({"statistics"}),
        allowed_commands=frozenset({sys.executable}),
    )
    workspace = ResearchWorkspace(request, workspace_root=tmp_path, approver=_approve, seed={"samples": [1, 3, 8]})

    first = workspace.run(
        "import statistics\n"
        "def summarize(values):\n"
        "    return statistics.mean(values)\n"
        "workspace_write_text('notes/result.txt', str(summarize(samples)))\n"
        "mean = summarize(samples)"
    )
    second = workspace.run(
        f"probe = run_subprocess([{sys.executable!r}, '-c', 'print(21 * 2)'])\n"
        "workspace_read_text('notes/result.txt'), mean, probe['stdout'].strip()"
    )

    assert first.error is None
    assert second.error is None
    assert "('4', 4, '42')" in second.stdout
    assert (tmp_path / "notes" / "result.txt").read_text() == "4"
    assert {item.name for item in workspace.variables()} >= {"samples", "mean", "summarize"}
    events = workspace.audit_events
    assert events[0].profile == "isolated_sandbox"
    assert "subprocess" in events[-1].capabilities
    workspace.close()


def test_path_network_and_secret_access_fail_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTOCONTEXT_TEST_SECRET", "do-not-expose")
    request = WorkspaceCapabilityRequest(
        workspace_id="denials",
        profile="isolated_sandbox",
        requested_capabilities=frozenset({"workspace_read", "workspace_write", "package_import"}),
        allowed_imports=frozenset({"json"}),
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
