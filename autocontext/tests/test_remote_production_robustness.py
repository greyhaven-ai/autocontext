from __future__ import annotations

import hashlib
import importlib
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from autocontext.config.settings import AppSettings, load_settings
from autocontext.execution.remote_execution import (
    RemoteCleanupOutcome,
    RemoteExecutionRequest,
    RemoteInputArtifact,
    RemoteResourceUsage,
    parse_remote_stdout,
)
from autocontext.execution.research_workspace import ResearchWorkspace
from autocontext.execution.research_workspace_models import (
    ResearchSandboxExecutionRequest,
    WorkspaceCapabilityRequest,
    WorkspaceResourceLimits,
    WorkspaceSecretGrant,
)
from autocontext.execution.research_workspace_runtime import run_in_child
from autocontext.execution.scenario_remote_package import (
    DEFAULT_REMOTE_RUNTIME_IMAGE,
    build_remote_scenario_package,
)
from autocontext.runtime_images import PINNED_PYTHON_RUNTIME_IMAGE


def _approve(_: WorkspaceCapabilityRequest) -> bool:
    return True


def test_import_facade_blocks_transitive_ambient_modules(tmp_path: Path) -> None:
    response = run_in_child(
        {
            "code": "import random\nrandom._os.environ",
            "variables": {},
            "helper_sources": (),
            "workspace_root": str(tmp_path),
            "profile": "isolated_sandbox",
            "capabilities": ("package_import",),
            "allowed_imports": ("random",),
            "allowed_commands": (),
            "allowed_network_hosts": (),
            "limits": WorkspaceResourceLimits(),
        },
        2.0,
    )

    assert response is not None
    assert "transitive module access denied: os" in str(response["error"])


def test_isolated_imports_cannot_be_combined_with_an_allowlisted_subprocess() -> None:
    with pytest.raises(ValueError, match="cannot combine package_import and subprocess"):
        WorkspaceCapabilityRequest(
            workspace_id="import-process-bypass",
            profile="isolated_sandbox",
            requested_capabilities=frozenset({"package_import", "subprocess"}),
            allowed_imports=frozenset({"statistics"}),
            allowed_commands=frozenset({"python"}),
        )


@pytest.mark.parametrize(
    "overrides",
    [
        {"profile": "isolatd_sandbox"},
        {"lifecycle": "delete_on_cloze"},
        {"workspace_id": "x" * 129},
    ],
)
def test_workspace_contract_rejects_fail_open_modes_and_unbounded_identities(
    overrides: dict[str, object],
) -> None:
    values: dict[str, object] = {"workspace_id": "workspace", **overrides}
    with pytest.raises(ValueError):
        WorkspaceCapabilityRequest(**values)  # type: ignore[arg-type]


def test_workspace_contract_rejects_nonfinite_limits_and_grant_expiry() -> None:
    with pytest.raises(ValueError, match="positive and finite"):
        WorkspaceResourceLimits(timeout_seconds=float("nan"))
    with pytest.raises(ValueError, match="expiry must be finite"):
        WorkspaceSecretGrant("dataset", "opaque-id", float("nan"))


def test_docker_rechecks_imported_callable_command_bypass_at_backend_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from autocontext.execution.docker_research_sandbox import DockerResearchSandboxBackend

    monkeypatch.setattr(shutil, "which", lambda _: "/usr/bin/docker")
    backend = DockerResearchSandboxBackend()
    adversarial = ResearchSandboxExecutionRequest(
        workspace_id="direct-backend-bypass",
        sequence=1,
        code="import statistics\nstatistics.mean.__globals__['os'].system('unlisted-command')",
        variables={},
        helper_sources=(),
        files={},
        granted_capabilities=frozenset({"package_import", "subprocess"}),
        allowed_imports=frozenset({"statistics"}),
        allowed_commands=frozenset({"python"}),
        allowed_network_hosts=frozenset(),
        secret_grants=(),
        limits=WorkspaceResourceLimits(),
    )

    with pytest.raises(PermissionError, match="cannot combine package_import and subprocess"):
        backend.execute(adversarial)


def test_docker_command_enforces_process_file_and_environment_grants(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from autocontext.execution.docker_research_sandbox import DockerResearchSandboxBackend

    monkeypatch.setattr(shutil, "which", lambda _: "/usr/bin/docker")
    backend = DockerResearchSandboxBackend()
    command = backend._docker_command(  # noqa: SLF001 - security contract regression
        "container",
        "workspace",
        tmp_path / "input",
        tmp_path / "output",
        tmp_path / "workspace",
        None,
        frozenset({"package_import"}),
    )

    assert command[command.index("--pids-limit") + 1] == "1"
    assert command[command.index("--pull") + 1] == "never"
    assert "-i" in command[command.index(DEFAULT_REMOTE_RUNTIME_IMAGE) :]
    assert not any("dst=/workspace" in item for item in command)
    assert "--env-file" not in command

    read_only = backend._docker_command(  # noqa: SLF001
        "container",
        "workspace",
        tmp_path / "input",
        tmp_path / "output",
        tmp_path / "workspace",
        None,
        frozenset({"workspace_read"}),
    )
    workspace_mount = next(item for item in read_only if "dst=/workspace" in item)
    assert workspace_mount.endswith(",readonly")
    with pytest.raises(ValueError, match="credential environment files are forbidden"):
        backend._docker_command(  # noqa: SLF001
            "container",
            "workspace",
            tmp_path / "input",
            tmp_path / "output",
            tmp_path / "workspace",
            tmp_path / "secrets.env",
        )


def test_docker_runtime_image_is_prepared_outside_the_candidate_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from autocontext.execution import docker_research_sandbox as docker_module

    monkeypatch.setattr(shutil, "which", lambda _: "/usr/bin/docker")
    calls: list[tuple[list[str], float]] = []
    responses = iter(
        (
            subprocess.CompletedProcess([], 1, stdout="", stderr="Error: No such image"),
            subprocess.CompletedProcess([], 0, stdout="pulled", stderr=""),
            subprocess.CompletedProcess([], 0, stdout="image-id", stderr=""),
        )
    )

    def fake_run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append((command, float(kwargs["timeout"])))
        return next(responses)

    monkeypatch.setattr(docker_module.subprocess, "run", fake_run)
    backend = docker_module.DockerResearchSandboxBackend(image_preparation_timeout_seconds=45.0)

    backend._ensure_image_available()  # noqa: SLF001 - provisioning boundary regression
    backend._ensure_image_available()  # noqa: SLF001 - cached per backend

    assert [command[1] for command, _ in calls] == ["image", "pull", "image"]
    assert all(timeout == 45.0 for _, timeout in calls)


def test_docker_runtime_image_preparation_timeout_is_an_infrastructure_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from autocontext.execution import docker_research_sandbox as docker_module

    monkeypatch.setattr(shutil, "which", lambda _: "/usr/bin/docker")

    def fake_run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        if command[1] == "image":
            return subprocess.CompletedProcess(command, 1, stdout="", stderr="Error: No such image")
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr(docker_module.subprocess, "run", fake_run)
    backend = docker_module.DockerResearchSandboxBackend(image_preparation_timeout_seconds=0.5)

    with pytest.raises(RuntimeError, match="image preparation timed out"):
        backend._ensure_image_available()  # noqa: SLF001


def test_docker_workspace_ownership_labels_do_not_alias_sanitized_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from autocontext.execution.docker_research_sandbox import DockerResearchSandboxBackend

    monkeypatch.setattr(shutil, "which", lambda _: "/usr/bin/docker")
    backend = DockerResearchSandboxBackend()
    labels: set[str] = set()
    for workspace_id in ("tenant/a", "tenant-a", "tenant a"):
        command = backend._docker_command(  # noqa: SLF001 - ownership boundary regression
            "container",
            workspace_id,
            Path("/input"),
            Path("/output"),
            Path("/workspace"),
            None,
        )
        labels.add(next(command[index + 1] for index, item in enumerate(command) if item == "--label"))

    assert len(labels) == 3


def test_docker_container_name_is_ascii_for_unicode_workspace_identity(
) -> None:
    from autocontext.execution.docker_research_sandbox import _container_name

    name = _container_name("研究", 1)

    assert name.isascii()
    assert name.startswith("autoctx-id-")


def test_docker_rejects_oversized_result_before_reading_it_into_the_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from autocontext.execution import docker_research_sandbox as docker_module

    monkeypatch.setattr(shutil, "which", lambda _: "/usr/bin/docker")

    def fake_run(command: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        if command[1] == "run":
            output_mount = next(item for item in command if "dst=/output" in item)
            output_root = Path(output_mount.split("src=", 1)[1].split(",dst=", 1)[0])
            (output_root / "result.json").write_bytes(b"x" * 33)
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(docker_module.subprocess, "run", fake_run)
    backend = docker_module.DockerResearchSandboxBackend()
    request = ResearchSandboxExecutionRequest(
        workspace_id="bounded-result",
        sequence=1,
        code="pass",
        variables={},
        helper_sources=(),
        files={},
        granted_capabilities=frozenset(),
        allowed_imports=frozenset(),
        allowed_commands=frozenset(),
        allowed_network_hosts=frozenset(),
        secret_grants=(),
        limits=WorkspaceResourceLimits(max_file_bytes=32),
    )

    with pytest.raises(RuntimeError, match="result artifact exceeds the per-file byte quota"):
        backend.execute(request)


def test_docker_rejects_value_resolvers_and_uses_a_narrow_host_broker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from autocontext.execution.docker_research_sandbox import DockerResearchSandboxBackend

    monkeypatch.setattr(shutil, "which", lambda _: "/usr/bin/docker")
    with pytest.raises(ValueError, match="value resolvers are unsafe"):
        DockerResearchSandboxBackend(secret_resolver=lambda _: "candidate-visible-secret")

    calls: list[tuple[str, str, dict[str, Any]]] = []

    def broker(grant: WorkspaceSecretGrant, operation: str, arguments: dict[str, Any]) -> dict[str, bool]:
        calls.append((grant.grant_id, operation, arguments))
        return {"accepted": True}

    backend = DockerResearchSandboxBackend(credential_broker=broker)
    grant = WorkspaceSecretGrant(
        name="papers",
        grant_id="opaque-ref",
        expires_at=time.time() + 60,
        allowed_operations=frozenset({"fetch_metadata"}),
    )

    assert backend.broker_call(grant, "fetch_metadata", {"paper_id": "p-1"}) == {"accepted": True}
    assert calls == [("opaque-ref", "fetch_metadata", {"paper_id": "p-1"})]
    with pytest.raises(PermissionError, match="not granted"):
        backend.broker_call(grant, "raw_token", {})


def test_prime_images_are_pinned_by_default_and_validated_at_settings_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert AppSettings().primeintellect_docker_image == PINNED_PYTHON_RUNTIME_IMAGE
    with pytest.raises(ValidationError, match="immutable @sha256"):
        AppSettings(primeintellect_docker_image="python:3.11-slim")
    with pytest.raises(ValidationError, match="immutable @sha256"):
        AppSettings(primeintellect_docker_image=f"python:3.11@tag@sha256:{'a' * 64}")

    monkeypatch.setenv("AUTOCONTEXT_PRIMEINTELLECT_DOCKER_IMAGE", "python:3.11-slim")
    with pytest.raises(ValidationError, match="immutable @sha256"):
        load_settings()


def test_custom_prime_api_base_is_rejected_instead_of_silently_ignored() -> None:
    with pytest.raises(ValidationError, match="custom AUTOCONTEXT_PRIMEINTELLECT_API_BASE"):
        AppSettings(primeintellect_api_base="https://prime-proxy.invalid")


def test_unknown_executor_mode_does_not_silently_fall_back_to_local() -> None:
    from autocontext.execution.runtime_factory import build_execution_runtime

    with pytest.raises(ValueError, match="Unsupported executor mode"):
        build_execution_runtime(AppSettings(executor_mode="primeintelect"))


def test_live_composition_settings_load_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AUTOCONTEXT_CONTEXT_BUNDLE_PROMOTION_ENABLED", "true")
    monkeypatch.setenv("AUTOCONTEXT_CONTEXT_BUNDLE_PROMOTION_MAX_CONFIRMATION_PAIRS", "12")
    monkeypatch.setenv("AUTOCONTEXT_CONTEXT_BUNDLE_PROMOTION_ROBUST_METHOD", "bounded_hoeffding")
    monkeypatch.setenv("AUTOCONTEXT_CAMPAIGN_AUDITOR_ENABLED", "true")
    monkeypatch.setenv("AUTOCONTEXT_CAMPAIGN_AUDITOR_MAX_CALLS_PER_CAMPAIGN", "3")

    settings = load_settings()

    assert settings.context_bundle_promotion_enabled is True
    assert settings.context_bundle_promotion_max_confirmation_pairs == 12
    assert settings.context_bundle_promotion_robust_method == "bounded_hoeffding"
    assert settings.campaign_auditor_enabled is True
    assert settings.campaign_auditor_max_calls_per_campaign == 3
    with pytest.raises(ValidationError, match="max_confirmation_pairs"):
        AppSettings(
            context_bundle_promotion_min_confirmation_pairs=8,
            context_bundle_promotion_max_confirmation_pairs=7,
        )


def test_custom_package_vendors_local_dependencies_and_restores_required_init_state(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    (tmp_path / "remote_required_helper.py").write_text(
        "def adjusted(value, bias):\n    return float(value) + float(bias)\n",
        encoding="utf-8",
    )
    (tmp_path / "remote_required_scenario.py").write_text(
        """from autocontext.scenarios.base import Observation, Result, ScenarioInterface
from remote_required_helper import adjusted

class RequiredScenario(ScenarioInterface):
    name = "required"
    def __init__(self, bias): self.bias = bias
    def describe_rules(self): return "required constructor"
    def describe_strategy_interface(self): return "value"
    def describe_evaluation_criteria(self): return "score"
    def initial_state(self, seed=None): return {"terminal": False, "seed": seed}
    def get_observation(self, state, player_id): return Observation(narrative=player_id)
    def validate_actions(self, state, player_id, actions): return ("value" in actions, "ok")
    def step(self, state, actions):
        return {**state, "terminal": True, "score": adjusted(actions["value"], self.bias)}
    def is_terminal(self, state): return state["terminal"]
    def get_result(self, state): return Result(score=state["score"], summary="ok", replay=[])
    def replay_to_narrative(self, replay): return "ok"
    def render_frame(self, state): return state
""",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    importlib.invalidate_caches()
    module = importlib.import_module("remote_required_scenario")
    package = build_remote_scenario_package(module.RequiredScenario(0.25), {"value": 0.5}, 9)
    package_path = tmp_path / "scenario.pyz"
    package_path.write_bytes(package.content)

    completed = subprocess.run(
        [sys.executable, "-I", str(package_path)],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
        timeout=5,
    )
    payload = json.loads(completed.stdout)

    assert payload["result"]["score"] == 0.75
    assert package.manifest["dependencies"] == ["remote_required_helper"]
    assert package.manifest["scenario_state_sha256"]
    assert package.manifest["strategy_sha256"]


@pytest.mark.parametrize(
    ("limits", "code", "message"),
    [
        (
            WorkspaceResourceLimits(max_file_bytes=10, max_workspace_bytes=5),
            "workspace_write_text('a', '123'); workspace_write_text('b', '456')",
            "aggregate byte quota",
        ),
        (
            WorkspaceResourceLimits(max_workspace_inodes=1),
            "workspace_write_text('nested/a', '1')",
            "aggregate inode quota",
        ),
    ],
)
def test_workspace_aggregate_quotas_fail_the_generation(
    tmp_path: Path,
    limits: WorkspaceResourceLimits,
    code: str,
    message: str,
) -> None:
    request = WorkspaceCapabilityRequest(
        workspace_id="quota",
        profile="trusted_local",
        requested_capabilities=frozenset({"workspace_write"}),
        limits=limits,
    )
    workspace = ResearchWorkspace(request, workspace_root=tmp_path, approver=_approve)

    result = workspace.run(code)

    assert message in (result.error or "")
    assert not any(tmp_path.rglob("*"))
    workspace.close()


def test_docker_startup_reconciles_orphans_and_propagates_removal_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from autocontext.execution import docker_research_sandbox as docker_module

    monkeypatch.setattr(shutil, "which", lambda _: "/usr/bin/docker")
    calls: list[list[str]] = []
    responses = iter(
        (
            subprocess.CompletedProcess([], 0, stdout="orphan-1\n", stderr=""),
            subprocess.CompletedProcess([], 0, stdout="orphan-1\t0\n", stderr=""),
            subprocess.CompletedProcess([], 0, stdout="", stderr=""),
            subprocess.CompletedProcess([], 0, stdout="", stderr=""),
        )
    )

    def successful_run(command: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return next(responses)

    monkeypatch.setattr(docker_module.subprocess, "run", successful_run)
    backend = docker_module.DockerResearchSandboxBackend()
    backend._ensure_startup_reconciled()  # noqa: SLF001 - startup recovery contract

    assert any(command[1:3] == ["rm", "-f"] and "orphan-1" in command for command in calls)

    protected_calls: list[list[str]] = []
    protected_responses = iter(
        (
            subprocess.CompletedProcess([], 0, stdout="live-1\n", stderr=""),
            subprocess.CompletedProcess([], 0, stdout=f"live-1\t{time.time() + 300}\n", stderr=""),
        )
    )

    def protected_run(command: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        protected_calls.append(command)
        return next(protected_responses)

    monkeypatch.setattr(docker_module.subprocess, "run", protected_run)
    docker_module.DockerResearchSandboxBackend()._ensure_startup_reconciled()  # noqa: SLF001
    assert not any(command[1:3] == ["rm", "-f"] for command in protected_calls)

    failure_responses = iter(
        (
            subprocess.CompletedProcess([], 0, stdout="orphan-2\n", stderr=""),
            subprocess.CompletedProcess([], 1, stdout="", stderr="permission denied"),
        )
    )
    monkeypatch.setattr(docker_module.subprocess, "run", lambda *args, **kwargs: next(failure_responses))
    failed = docker_module.DockerResearchSandboxBackend().cleanup("workspace")

    assert failed.succeeded is False
    assert "container removal failed" in failed.detail


def test_docker_startup_reconciliation_tolerates_containers_exiting_during_inspect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from autocontext.execution import docker_research_sandbox as docker_module

    monkeypatch.setattr(shutil, "which", lambda _: "/usr/bin/docker")
    responses = iter(
        (
            subprocess.CompletedProcess([], 0, stdout="finished\nlive\n", stderr=""),
            subprocess.CompletedProcess(
                [],
                1,
                stdout=f"live\t{time.time() + 300}\n",
                stderr="Error: No such object: finished\n",
            ),
        )
    )
    monkeypatch.setattr(docker_module.subprocess, "run", lambda *args, **kwargs: next(responses))

    docker_module.DockerResearchSandboxBackend()._ensure_startup_reconciled()  # noqa: SLF001


def test_workspace_close_propagates_runtime_cleanup_errors(tmp_path: Path) -> None:
    workspace = ResearchWorkspace(WorkspaceCapabilityRequest(workspace_id="cleanup"), workspace_root=tmp_path)

    class FailingRuntime:
        def cleanup(self) -> None:
            raise OSError("cannot release mount")

    workspace.runtime_env = FailingRuntime()  # type: ignore[assignment]
    cleanup = workspace.close()

    assert cleanup.outcome == "error"
    assert "cannot release mount" in cleanup.detail


def test_malformed_scenario_output_is_infrastructure_failure_with_provenance() -> None:
    package = b"scenario-package"
    request = RemoteExecutionRequest(
        task_id="scenario:custom:17",
        image=DEFAULT_REMOTE_RUNTIME_IMAGE,
        command="python autocontext-scenario.pyz",
        input_artifacts=(RemoteInputArtifact("autocontext-scenario.pyz", package),),
        metadata={
            "task_kind": "scenario_match",
            "scenario": "custom",
            "seed": "17",
            "package_sha256": hashlib.sha256(package).hexdigest(),
        },
    )

    result = parse_remote_stdout(
        request,
        provider="fake",
        stdout="{}",
        stderr="",
        exit_code=0,
        usage=RemoteResourceUsage(),
        cleanup=RemoteCleanupOutcome(True, True, "sandbox-1"),
        session_id="sandbox-1",
    )

    assert result.status == "artifact_error"
    assert result.to_ledger_entry().infrastructure_succeeded is False
    assert result.provenance.image == DEFAULT_REMOTE_RUNTIME_IMAGE
    assert result.provenance.package_sha256 == hashlib.sha256(package).hexdigest()
    assert result.provenance.inputs[0].sha256 == hashlib.sha256(package).hexdigest()
    assert result.provenance.seed == 17
    assert result.to_ledger_entry().provenance == result.provenance


def test_any_malformed_output_artifact_prevents_success() -> None:
    request = RemoteExecutionRequest(task_id="artifact", image="image:tag", command="true")

    result = parse_remote_stdout(
        request,
        provider="fake",
        stdout='{"artifacts":{"corrupt.bin":{"base64":"not!base64"}}}',
        stderr="",
        exit_code=0,
        usage=RemoteResourceUsage(),
        cleanup=RemoteCleanupOutcome(True, True, "sandbox-1"),
        session_id="sandbox-1",
    )

    assert result.status == "artifact_error"
    assert "invalid base64" in result.error
    assert result.to_ledger_entry().infrastructure_succeeded is False
