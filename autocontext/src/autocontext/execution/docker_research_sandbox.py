"""Concrete Docker isolation backend for ``ResearchWorkspace`` (AC-981)."""

from __future__ import annotations

import base64
import hashlib
import json
import math
import os
import pickle
import shutil
import subprocess
import tempfile
import threading
import time
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
    WorkspaceCredentialBroker,
    WorkspaceSecretGrant,
)
from autocontext.execution.scenario_remote_package import (
    DEFAULT_REMOTE_RUNTIME_IMAGE,
    require_pinned_runtime_image,
)

SecretGrantResolver = Callable[[WorkspaceSecretGrant], str]
CredentialBroker = WorkspaceCredentialBroker
_EXPIRY_LABEL = "ai.autocontext.expires-at"
_CLEANUP_GRACE_SECONDS = 30.0
_DEFAULT_IMAGE_PREPARATION_TIMEOUT_SECONDS = 120.0


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
        credential_broker: CredentialBroker | None = None,
        memory_mb: int = 512,
        cpu_count: float = 1.0,
        pids_limit: int = 64,
        image_preparation_timeout_seconds: float = _DEFAULT_IMAGE_PREPARATION_TIMEOUT_SECONDS,
        source_root: Path | None = None,
    ) -> None:
        require_pinned_runtime_image(image)
        if memory_mb < 64 or not math.isfinite(cpu_count) or cpu_count <= 0 or pids_limit < 2:
            raise ValueError("Docker sandbox resource limits are invalid")
        if not math.isfinite(image_preparation_timeout_seconds) or image_preparation_timeout_seconds <= 0:
            raise ValueError("Docker image preparation timeout must be positive and finite")
        if secret_resolver is not None:
            raise ValueError("Docker secret value resolvers are unsafe; use a host-side credential_broker with opaque grants")
        resolved_binary = shutil.which(docker_binary)
        if resolved_binary is None:
            raise RuntimeError(f"Docker sandbox executable is unavailable: {docker_binary}")
        self.image = image
        self.docker_binary = resolved_binary
        self.credential_broker = credential_broker
        self.memory_mb = memory_mb
        self.cpu_count = cpu_count
        self.pids_limit = pids_limit
        self.image_preparation_timeout_seconds = image_preparation_timeout_seconds
        self.source_root = (source_root or Path(__file__).resolve().parents[2]).resolve()
        if not (self.source_root / "autocontext" / "execution" / "research_workspace_runtime.py").is_file():
            raise RuntimeError("Docker sandbox could not locate the autocontext Python source root")
        self._active: dict[str, set[str]] = {}
        self._prepared_workspaces: set[str] = set()
        self._image_ready = False
        self._lock = threading.RLock()

    def capabilities(self) -> SandboxBackendCapabilities:
        return SandboxBackendCapabilities(
            backend_name="docker",
            os_isolation=True,
            workspace_mounts=True,
            network_policy=True,
            process_limits=True,
            environment_scrubbing=True,
            secret_grants=self.credential_broker is not None,
            transactional_files=True,
            terminable_execution=True,
            cleanup_verification=True,
        )

    def execute(self, request: ResearchSandboxExecutionRequest) -> ResearchSandboxExecutionResult:
        if {"package_import", "subprocess"}.issubset(request.granted_capabilities):
            raise PermissionError(
                "Docker sandbox cannot combine package_import and subprocess; imported callables bypass command allowlists"
            )
        if "network" in request.granted_capabilities or request.allowed_network_hosts:
            raise PermissionError(
                "DockerResearchSandboxBackend is deny-network only; configure an egress-policy backend for network grants"
            )
        if request.secret_grants and self.credential_broker is None:
            raise PermissionError("Docker sandbox secret grants require a host-side credential broker")
        self._ensure_image_available()
        self._ensure_startup_reconciled()
        with self._lock:
            prepared = request.workspace_id in self._prepared_workspaces
        if not prepared:
            cleanup = self.cleanup(request.workspace_id)
            if not cleanup.succeeded:
                raise RuntimeError(cleanup.detail)
            with self._lock:
                self._prepared_workspaces.add(request.workspace_id)
        container_name = _container_name(request.workspace_id, request.sequence)
        with tempfile.TemporaryDirectory(prefix="autocontext-docker-sandbox-") as directory:
            root = Path(directory)
            input_root = root / "input"
            output_root = root / "output"
            workspace_root = root / "workspace"
            input_root.mkdir()
            output_root.mkdir()
            workspace_root.mkdir()
            if "workspace_read" in request.granted_capabilities:
                restore_files(
                    workspace_root,
                    request.files,
                    request.limits.max_file_bytes,
                    request.limits.max_workspace_bytes,
                    request.limits.max_workspace_inodes,
                )
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
                "profile": "isolated_sandbox",
                "capabilities": tuple(request.granted_capabilities),
                "allowed_imports": tuple(request.allowed_imports),
                "allowed_commands": tuple(request.allowed_commands),
                "allowed_network_hosts": (),
                "limits": request.limits,
            }
            (input_root / "payload.pkl").write_bytes(pickle.dumps(payload, protocol=5))
            (input_root / "runner.py").write_text(_CONTAINER_RUNNER, encoding="utf-8")
            command = self._docker_command(
                container_name,
                request.workspace_id,
                input_root,
                output_root,
                workspace_root,
                None,
                request.granted_capabilities,
                expires_at=time.time() + request.limits.timeout_seconds + _CLEANUP_GRACE_SECONDS,
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
                    try:
                        self._remove_containers((container_name,))
                    except RuntimeError as cleanup_exc:
                        raise RuntimeError(f"Docker sandbox timed out and cleanup failed: {cleanup_exc}") from exc
                    raise TimeoutError("Docker sandbox execution timed out and was terminated") from exc
                if completed.returncode != 0:
                    detail = (completed.stderr or completed.stdout).strip()
                    raise RuntimeError(f"Docker sandbox failed: {detail[-500:]}")
                output_path = output_root / "result.json"
                if not output_path.is_file():
                    raise RuntimeError("Docker sandbox did not produce its result artifact")
                output_size = output_path.stat().st_size
                if output_size > request.limits.max_file_bytes:
                    raise RuntimeError(
                        "Docker sandbox result artifact exceeds the per-file byte quota: "
                        f"{output_size} > {request.limits.max_file_bytes}"
                    )
                raw = _wire_decode(json.loads(output_path.read_text(encoding="utf-8")))
                if not isinstance(raw, Mapping):
                    raise RuntimeError("Docker sandbox result artifact is invalid")
                response = ResearchSandboxExecutionResult(
                    stdout=str(raw.get("stdout", "")),
                    error=str(raw["error"]) if raw.get("error") else None,
                    answer=dict(raw.get("answer", {})),
                    variables=dict(raw.get("variables", {})),
                    helper_sources=tuple(raw.get("helper_sources", ())),
                    files=(
                        snapshot_files(
                            workspace_root,
                            request.limits.max_file_bytes,
                            request.limits.max_workspace_bytes,
                            request.limits.max_workspace_inodes,
                        )
                        if "workspace_write" in request.granted_capabilities
                        else dict(request.files)
                    ),
                    session_id=container_name,
                    detail="backend=docker network=deny rootfs=read-only",
                )
                return response
            finally:
                with self._lock:
                    self._active.get(request.workspace_id, set()).discard(container_name)

    def cleanup(self, workspace_id: str) -> SandboxBackendCleanupResult:
        label = f"ai.autocontext.workspace={_workspace_label(workspace_id)}"
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
        except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
            return SandboxBackendCleanupResult(
                False,
                f"Docker cleanup verification failed: {type(exc).__name__}: {str(exc)[-240:]}",
            )
        if verify.stdout.strip():
            return SandboxBackendCleanupResult(False, "Docker cleanup left workspace containers behind")
        return SandboxBackendCleanupResult(True, "Docker workspace containers removed and verified")

    def broker_call(
        self,
        grant: WorkspaceSecretGrant,
        operation: str,
        arguments: Mapping[str, Any],
    ) -> Any:
        """Perform a scoped credentialed operation entirely in the host plane."""

        if self.credential_broker is None:
            raise PermissionError("Docker credential broker is unavailable")
        if grant.expires_at <= time.time():
            raise PermissionError(f"workspace secret grant is expired: {grant.name}")
        if operation not in grant.allowed_operations:
            raise PermissionError(f"credential broker operation is not granted: {operation}")
        return self.credential_broker(grant, operation, dict(arguments))

    def _ensure_startup_reconciled(self) -> None:
        """Remove only containers whose execution deadline has expired."""

        with self._lock:
            try:
                listed = subprocess.run(  # noqa: S603
                    [self.docker_binary, "ps", "-aq", "--filter", "label=ai.autocontext.workspace"],
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=10.0,
                )
                candidates = tuple(item for item in listed.stdout.splitlines() if item.strip())
                expired = self._expired_containers(candidates, now=time.time())
                self._remove_containers(expired)
                if not expired:
                    return
                verify = subprocess.run(  # noqa: S603
                    [self.docker_binary, "ps", "-aq", "--filter", "label=ai.autocontext.workspace"],
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=10.0,
                )
            except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
                raise RuntimeError(f"Docker startup orphan reconciliation failed: {type(exc).__name__}: {exc}") from exc
            remaining = {item for item in verify.stdout.splitlines() if item.strip()}
            if remaining.intersection(expired):
                raise RuntimeError("Docker startup orphan reconciliation left expired containers behind")

    def _ensure_image_available(self) -> None:
        """Provision the pinned runtime image outside the candidate deadline."""

        with self._lock:
            if self._image_ready:
                return
            environment = {
                key: os.environ[key]
                for key in ("PATH", "HOME", "DOCKER_HOST", "DOCKER_CONFIG")
                if key in os.environ
            }
            inspect_command = [self.docker_binary, "image", "inspect", self.image]
            try:
                inspected = subprocess.run(  # noqa: S603
                    inspect_command,
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=self.image_preparation_timeout_seconds,
                    env=environment,
                )
            except (OSError, subprocess.SubprocessError) as exc:
                raise RuntimeError(f"Docker runtime image inspection failed: {type(exc).__name__}: {exc}") from exc
            if inspected.returncode != 0:
                detail = (inspected.stderr or inspected.stdout).strip()
                normalized = detail.casefold()
                if "no such image" not in normalized and "no such object" not in normalized:
                    raise RuntimeError(f"Docker runtime image inspection failed: {detail[-240:]}")
                try:
                    pulled = subprocess.run(  # noqa: S603
                        [self.docker_binary, "pull", self.image],
                        check=False,
                        capture_output=True,
                        text=True,
                        timeout=self.image_preparation_timeout_seconds,
                        env=environment,
                    )
                except subprocess.TimeoutExpired as exc:
                    raise RuntimeError("Docker runtime image preparation timed out") from exc
                except (OSError, subprocess.SubprocessError) as exc:
                    raise RuntimeError(f"Docker runtime image preparation failed: {type(exc).__name__}: {exc}") from exc
                if pulled.returncode != 0:
                    detail = (pulled.stderr or pulled.stdout).strip()
                    raise RuntimeError(f"Docker runtime image preparation failed: {detail[-240:]}")
                try:
                    inspected = subprocess.run(  # noqa: S603
                        inspect_command,
                        check=False,
                        capture_output=True,
                        text=True,
                        timeout=self.image_preparation_timeout_seconds,
                        env=environment,
                    )
                except (OSError, subprocess.SubprocessError) as exc:
                    raise RuntimeError(
                        f"Docker runtime image verification failed: {type(exc).__name__}: {exc}"
                    ) from exc
                if inspected.returncode != 0:
                    detail = (inspected.stderr or inspected.stdout).strip()
                    raise RuntimeError(f"Docker runtime image verification failed: {detail[-240:]}")
            self._image_ready = True

    def _expired_containers(self, container_ids: tuple[str, ...], *, now: float) -> tuple[str, ...]:
        if not container_ids:
            return ()
        inspected = subprocess.run(  # noqa: S603
            [
                self.docker_binary,
                "inspect",
                "--format",
                f'{{{{.Id}}}}\t{{{{ index .Config.Labels "{_EXPIRY_LABEL}" }}}}',
                *container_ids,
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=10.0,
        )
        if inspected.returncode != 0:
            errors = [line for line in inspected.stderr.splitlines() if line.strip()]
            # A container may exit normally between `docker ps` and `inspect`.
            # That race is already reconciled and must not make unrelated new
            # executions unavailable. Every other daemon/permission error is
            # still terminal.
            if not errors or not all(
                "No such object" in line or "No such container" in line for line in errors
            ):
                detail = (inspected.stderr or inspected.stdout).strip()
                raise RuntimeError(f"Docker orphan inspection failed: {detail[-240:]}")
        expired: list[str] = []
        known_ids = set(container_ids)
        for line in inspected.stdout.splitlines():
            inspected_id, separator, raw_expiry = line.partition("\t")
            if not separator:
                raise RuntimeError("Docker orphan reconciliation returned malformed expiry metadata")
            container_id = next(
                (candidate for candidate in known_ids if inspected_id.startswith(candidate)),
                "",
            )
            if not container_id:
                raise RuntimeError("Docker orphan reconciliation returned an unexpected container id")
            try:
                expires_at = float(raw_expiry)
            except ValueError:
                # Containers without this version's deadline label may belong
                # to another live/rolling-upgrade worker. Never guess that they
                # are abandoned.
                continue
            if math.isfinite(expires_at) and expires_at <= now:
                expired.append(container_id)
        return tuple(expired)

    def _docker_command(
        self,
        container_name: str,
        workspace_id: str,
        input_root: Path,
        output_root: Path,
        workspace_root: Path,
        env_file: Path | None,
        granted_capabilities: frozenset[str] = frozenset({"workspace_read", "workspace_write", "subprocess"}),
        *,
        expires_at: float | None = None,
    ) -> list[str]:
        if env_file is not None:
            raise ValueError("candidate-visible credential environment files are forbidden")
        pids_limit = self.pids_limit if "subprocess" in granted_capabilities else 1
        command = [
            self.docker_binary,
            "run",
            "--pull",
            "never",
            "--rm",
            "--name",
            container_name,
            "--label",
            f"ai.autocontext.workspace={_workspace_label(workspace_id)}",
        ]
        if expires_at is not None:
            if not math.isfinite(expires_at) or expires_at <= 0:
                raise ValueError("Docker sandbox expiry must be a positive finite timestamp")
            command.extend(("--label", f"{_EXPIRY_LABEL}={expires_at:.6f}"))
        command.extend(
            (
                "--read-only",
                "--network",
                "none",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges",
                "--pids-limit",
                str(pids_limit),
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
                "--env",
                "LANG=C.UTF-8",
                "--env",
                "HOME=/tmp",
                "--user",
                f"{os.getuid()}:{os.getgid()}",
            )
        )
        if "workspace_read" in granted_capabilities or "workspace_write" in granted_capabilities:
            workspace_mount = f"type=bind,src={workspace_root},dst=/workspace"
            if "workspace_write" not in granted_capabilities:
                workspace_mount += ",readonly"
            command.extend(("--mount", workspace_mount))
        command.extend(
            (
                self.image,
                "env",
                "-i",
                "LANG=C.UTF-8",
                "HOME=/tmp",
                "PATH=/usr/local/bin:/usr/bin:/bin",
                "python",
                "-I",
                "/input/runner.py",
            )
        )
        return command

    def _remove_containers(self, identifiers: tuple[str, ...]) -> None:
        unique = sorted({identifier for identifier in identifiers if identifier})
        if not unique:
            return
        completed = subprocess.run(  # noqa: S603
            [self.docker_binary, "rm", "-f", *unique],
            check=False,
            capture_output=True,
            text=True,
            timeout=10.0,
        )
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout).strip()
            # Two workers may independently discover the same expired
            # container. Docker reports a nonzero exit after the first worker
            # removes it; that is already the desired terminal state.
            if detail and all("No such container" in line for line in detail.splitlines()):
                return
            raise RuntimeError(f"Docker container removal failed: {detail[-240:]}")


def _container_name(workspace_id: str, sequence: int) -> str:
    return f"autoctx-{_safe_label(workspace_id)[:32]}-{sequence}-{uuid.uuid4().hex[:10]}"


def _safe_label(value: str) -> str:
    rendered = "".join(
        char if (char.isascii() and char.isalnum()) or char in "_.-" else "-"
        for char in value
    ).strip("-.")
    if not rendered:
        rendered = f"id-{hashlib.sha256(value.encode('utf-8')).hexdigest()[:24]}"
    return rendered


def _workspace_label(value: str) -> str:
    """Return an injective Docker-safe encoding of workspace ownership."""

    encoded = base64.urlsafe_b64encode(value.encode("utf-8")).decode("ascii").rstrip("=")
    return f"v2-{encoded}"


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


__all__ = ["CredentialBroker", "DockerResearchSandboxBackend", "SecretGrantResolver"]
