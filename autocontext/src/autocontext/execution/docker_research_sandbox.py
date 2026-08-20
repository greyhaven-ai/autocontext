"""Concrete Docker isolation backend for ``ResearchWorkspace`` (AC-981)."""

from __future__ import annotations

import base64
import json
import os
import pickle
import shutil
import subprocess
import tempfile
import threading
import uuid
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from autocontext.execution.research_workspace_files import restore_files, snapshot_files
from autocontext.execution.research_workspace_models import (
    ResearchSandboxExecutionRequest,
    ResearchSandboxExecutionResult,
    SandboxBackendCapabilities,
    SandboxBackendCleanupResult,
    WorkspaceSecretGrant,
)
from autocontext.execution.scenario_remote_package import (
    DEFAULT_REMOTE_RUNTIME_IMAGE,
    require_pinned_runtime_image,
)

SecretGrantResolver = Callable[[WorkspaceSecretGrant], str]


class DockerResearchSandboxBackend:
    """Run capable workspace generations in a locked-down Linux container.

    The backend is deny-network only. Deployments that need allowlisted egress
    must provide a backend with a kernel/network-policy enforcement layer; an
    isolated request never degrades to the local child-process kernel.
    """

    def __init__(
        self,
        *,
        image: str = DEFAULT_REMOTE_RUNTIME_IMAGE,
        docker_binary: str = "docker",
        secret_resolver: SecretGrantResolver | None = None,
        memory_mb: int = 512,
        cpu_count: float = 1.0,
        pids_limit: int = 64,
        source_root: Path | None = None,
    ) -> None:
        require_pinned_runtime_image(image)
        if memory_mb < 64 or cpu_count <= 0 or pids_limit < 2:
            raise ValueError("Docker sandbox resource limits are invalid")
        resolved_binary = shutil.which(docker_binary)
        if resolved_binary is None:
            raise RuntimeError(f"Docker sandbox executable is unavailable: {docker_binary}")
        self.image = image
        self.docker_binary = resolved_binary
        self.secret_resolver = secret_resolver
        self.memory_mb = memory_mb
        self.cpu_count = cpu_count
        self.pids_limit = pids_limit
        self.source_root = (source_root or Path(__file__).resolve().parents[2]).resolve()
        if not (self.source_root / "autocontext" / "execution" / "research_workspace_runtime.py").is_file():
            raise RuntimeError("Docker sandbox could not locate the autocontext Python source root")
        self._active: dict[str, set[str]] = {}
        self._prepared_workspaces: set[str] = set()
        self._lock = threading.RLock()

    def capabilities(self) -> SandboxBackendCapabilities:
        return SandboxBackendCapabilities(
            backend_name="docker",
            os_isolation=True,
            workspace_mounts=True,
            network_policy=True,
            process_limits=True,
            environment_scrubbing=True,
            secret_grants=self.secret_resolver is not None,
            transactional_files=True,
            terminable_execution=True,
            cleanup_verification=True,
        )

    def execute(self, request: ResearchSandboxExecutionRequest) -> ResearchSandboxExecutionResult:
        if "network" in request.granted_capabilities or request.allowed_network_hosts:
            raise PermissionError(
                "DockerResearchSandboxBackend is deny-network only; configure an egress-policy backend for network grants"
            )
        if request.secret_grants and self.secret_resolver is None:
            raise PermissionError("Docker sandbox secret grants require a host secret resolver")
        with self._lock:
            prepared = request.workspace_id in self._prepared_workspaces
        if not prepared:
            cleanup = self.cleanup(request.workspace_id)
            if not cleanup.succeeded:
                raise RuntimeError(cleanup.detail)
            with self._lock:
                self._prepared_workspaces.add(request.workspace_id)
        container_name = _container_name(request.workspace_id, request.sequence)
        secret_values: list[str] = []
        with tempfile.TemporaryDirectory(prefix="autocontext-docker-sandbox-") as directory:
            root = Path(directory)
            input_root = root / "input"
            output_root = root / "output"
            workspace_root = root / "workspace"
            input_root.mkdir()
            output_root.mkdir()
            workspace_root.mkdir()
            restore_files(workspace_root, request.files, request.limits.max_file_bytes)
            runtime_root = input_root / "runtime"
            runtime_root.mkdir()
            for relative in (
                "autocontext/execution/research_workspace_models.py",
                "autocontext/execution/research_workspace_runtime.py",
                "autocontext/offline.py",
            ):
                source = self.source_root / relative
                (runtime_root / Path(relative).name).write_bytes(source.read_bytes())
            payload = {
                "code": request.code,
                "variables": dict(request.variables),
                "helper_sources": request.helper_sources,
                "workspace_root": "/workspace",
                "profile": "trusted_local",
                "capabilities": tuple(request.granted_capabilities),
                "allowed_imports": tuple(request.allowed_imports),
                "allowed_commands": tuple(request.allowed_commands),
                "allowed_network_hosts": (),
                "limits": request.limits,
            }
            (input_root / "payload.pkl").write_bytes(pickle.dumps(payload, protocol=5))
            (input_root / "runner.py").write_text(_CONTAINER_RUNNER, encoding="utf-8")
            env_file: Path | None = None
            if request.secret_grants:
                env_file = root / "secrets.env"
                lines: list[str] = []
                assert self.secret_resolver is not None
                for grant in request.secret_grants:
                    value = self.secret_resolver(grant)
                    if not value or "\n" in value or "\r" in value:
                        raise ValueError(f"secret resolver returned an invalid value for grant: {grant.name}")
                    secret_values.append(value)
                    lines.append(f"{grant.env_var}={value}")
                env_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
                os.chmod(env_file, 0o600)

            command = self._docker_command(
                container_name,
                request.workspace_id,
                input_root,
                output_root,
                workspace_root,
                env_file,
            )
            with self._lock:
                self._active.setdefault(request.workspace_id, set()).add(container_name)
            try:
                try:
                    host_environment = {
                        key: os.environ[key] for key in ("PATH", "HOME", "DOCKER_HOST", "DOCKER_CONFIG") if key in os.environ
                    }
                    completed = subprocess.run(  # noqa: S603
                        command,
                        check=False,
                        capture_output=True,
                        text=True,
                        timeout=request.limits.timeout_seconds,
                        env=host_environment,
                    )
                except subprocess.TimeoutExpired as exc:
                    self._remove_containers((container_name,))
                    raise TimeoutError("Docker sandbox execution timed out and was terminated") from exc
                if completed.returncode != 0:
                    detail = _redact_values((completed.stderr or completed.stdout).strip(), secret_values)
                    raise RuntimeError(f"Docker sandbox failed: {detail[-500:]}")
                output_path = output_root / "result.json"
                if not output_path.is_file():
                    raise RuntimeError("Docker sandbox did not produce its result artifact")
                raw = _wire_decode(json.loads(output_path.read_text(encoding="utf-8")))
                if not isinstance(raw, Mapping):
                    raise RuntimeError("Docker sandbox result artifact is invalid")
                response = ResearchSandboxExecutionResult(
                    stdout=str(raw.get("stdout", "")),
                    error=str(raw["error"]) if raw.get("error") else None,
                    answer=dict(raw.get("answer", {})),
                    variables=dict(raw.get("variables", {})),
                    helper_sources=tuple(raw.get("helper_sources", ())),
                    files=snapshot_files(workspace_root, request.limits.max_file_bytes),
                    session_id=container_name,
                    detail="backend=docker network=deny rootfs=read-only",
                )
                if _contains_secret(response, secret_values):
                    return ResearchSandboxExecutionResult(
                        error="SandboxSecurityError: resolved secret appeared in candidate output or persisted state",
                        session_id=container_name,
                        detail="backend=docker secret-output-rejected",
                    )
                return response
            finally:
                with self._lock:
                    self._active.get(request.workspace_id, set()).discard(container_name)

    def cleanup(self, workspace_id: str) -> SandboxBackendCleanupResult:
        label = f"ai.autocontext.workspace={_safe_label(workspace_id)}"
        with self._lock:
            active = tuple(self._active.pop(workspace_id, set()))
            self._prepared_workspaces.discard(workspace_id)
        try:
            listed = subprocess.run(  # noqa: S603
                [self.docker_binary, "ps", "-aq", "--filter", f"label={label}"],
                check=True,
                capture_output=True,
                text=True,
                timeout=10.0,
            )
            container_ids = tuple(item for item in listed.stdout.splitlines() if item.strip())
            self._remove_containers((*active, *container_ids))
            verify = subprocess.run(  # noqa: S603
                [self.docker_binary, "ps", "-aq", "--filter", f"label={label}"],
                check=True,
                capture_output=True,
                text=True,
                timeout=10.0,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return SandboxBackendCleanupResult(False, f"Docker cleanup verification failed: {type(exc).__name__}")
        if verify.stdout.strip():
            return SandboxBackendCleanupResult(False, "Docker cleanup left workspace containers behind")
        return SandboxBackendCleanupResult(True, "Docker workspace containers removed and verified")

    def _docker_command(
        self,
        container_name: str,
        workspace_id: str,
        input_root: Path,
        output_root: Path,
        workspace_root: Path,
        env_file: Path | None,
    ) -> list[str]:
        command = [
            self.docker_binary,
            "run",
            "--rm",
            "--name",
            container_name,
            "--label",
            f"ai.autocontext.workspace={_safe_label(workspace_id)}",
            "--read-only",
            "--network",
            "none",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--pids-limit",
            str(self.pids_limit),
            "--memory",
            f"{self.memory_mb}m",
            "--cpus",
            str(self.cpu_count),
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,nodev,size=16m",
            "--mount",
            f"type=bind,src={input_root},dst=/input,readonly",
            "--mount",
            f"type=bind,src={output_root},dst=/output",
            "--mount",
            f"type=bind,src={workspace_root},dst=/workspace",
            "--env",
            "LANG=C.UTF-8",
            "--env",
            "HOME=/tmp",
            "--user",
            f"{os.getuid()}:{os.getgid()}",
        ]
        if env_file is not None:
            command.extend(("--env-file", str(env_file)))
        command.extend((self.image, "python", "-I", "/input/runner.py"))
        return command

    def _remove_containers(self, identifiers: tuple[str, ...]) -> None:
        unique = sorted({identifier for identifier in identifiers if identifier})
        if not unique:
            return
        subprocess.run(  # noqa: S603
            [self.docker_binary, "rm", "-f", *unique],
            check=False,
            capture_output=True,
            text=True,
            timeout=10.0,
        )


def _container_name(workspace_id: str, sequence: int) -> str:
    return f"autoctx-{_safe_label(workspace_id)[:32]}-{sequence}-{uuid.uuid4().hex[:10]}"


def _safe_label(value: str) -> str:
    rendered = "".join(char if char.isalnum() or char in "_.-" else "-" for char in value).strip("-.")
    if not rendered:
        raise ValueError("Docker workspace identity has no safe label characters")
    return rendered


def _redact_values(value: str, secrets: list[str]) -> str:
    redacted = value
    for secret in secrets:
        redacted = redacted.replace(secret, "[REDACTED-SECRET]")
    return redacted


def _contains_secret(response: ResearchSandboxExecutionResult, secrets: list[str]) -> bool:
    rendered = repr(response)
    return any(secret in rendered for secret in secrets)


def _wire_decode(value: Any) -> Any:
    if isinstance(value, list):
        return [_wire_decode(item) for item in value]
    if not isinstance(value, dict):
        return value
    marker = value.get("__autoctx_type__")
    if marker == "bytes":
        return base64.b64decode(str(value["base64"]), validate=True)
    if marker in {"tuple", "set", "frozenset"}:
        items = [_wire_decode(item) for item in value["items"]]
        return tuple(items) if marker == "tuple" else set(items) if marker == "set" else frozenset(items)
    if marker == "mapping":
        return {_wire_decode(pair[0]): _wire_decode(pair[1]) for pair in value["items"]}
    return {str(key): _wire_decode(item) for key, item in value.items()}


_CONTAINER_RUNNER = r"""from __future__ import annotations
import base64
import importlib.util
import json
import pickle
import sys
import types
from pathlib import Path

def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load sandbox runtime module: {name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module

autocontext_package = types.ModuleType("autocontext")
autocontext_package.__path__ = ["/input/runtime"]
execution_package = types.ModuleType("autocontext.execution")
execution_package.__path__ = ["/input/runtime"]
sys.modules["autocontext"] = autocontext_package
sys.modules["autocontext.execution"] = execution_package
load_module(
    "autocontext.execution.research_workspace_models",
    "/input/runtime/research_workspace_models.py",
)
load_module("autocontext.offline", "/input/runtime/offline.py")
runtime = load_module(
    "autocontext.execution.research_workspace_runtime",
    "/input/runtime/research_workspace_runtime.py",
)
_execute_child = runtime._execute_child
new_helper_sources = runtime.new_helper_sources

def wire_encode(value):
    if value is None or type(value) in {bool, int, float, str}:
        return value
    if type(value) is bytes:
        return {"__autoctx_type__": "bytes", "base64": base64.b64encode(value).decode("ascii")}
    if type(value) in {tuple, set, frozenset}:
        return {"__autoctx_type__": type(value).__name__, "items": [wire_encode(item) for item in value]}
    if type(value) is list:
        return [wire_encode(item) for item in value]
    if type(value) is dict:
        return {
            "__autoctx_type__": "mapping",
            "items": [[wire_encode(key), wire_encode(item)] for key, item in value.items()],
        }
    raise TypeError(f"unsupported sandbox wire value: {type(value).__name__}")

payload = pickle.loads(Path("/input/payload.pkl").read_bytes())
response = _execute_child(payload)
helpers = tuple(payload.get("helper_sources", ()))
if not response.get("error"):
    helpers = (*helpers, *new_helper_sources(str(payload["code"]), helpers))
response["helper_sources"] = helpers
Path("/output/result.json").write_text(json.dumps(wire_encode(response), sort_keys=True), encoding="utf-8")
"""


__all__ = ["DockerResearchSandboxBackend", "SecretGrantResolver"]
