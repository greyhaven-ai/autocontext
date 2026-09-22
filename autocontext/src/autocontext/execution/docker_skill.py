"""JSON-only skill adapter over the shared, deny-network Docker boundary.

Candidate code runs only in the pinned container. No host-writable mount, result
file, pickle, local-exec fallback, or model interface crosses this seam.
"""

from __future__ import annotations

import math
import os
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from autocontext.artifacts.policy_candidate import CandidateLimits
from autocontext.execution.docker_isolation import (
    DockerIsolationLimits,
    build_docker_isolation_command,
    sanitized_docker_environment,
)
from autocontext.kernel_evolution._process_control import BoundedOutput, drain_bounded
from autocontext.runtime_images import PINNED_PYTHON_RUNTIME_IMAGE

# Inputs/source are already bounded by the bridge. Treat every byte returned by
# this process as untrusted, even if a skill replaces its Python interpreter state.
RUNNER = """
import json
import pathlib
import sys

namespace = {}
source = pathlib.Path('/input/skill.py').read_text()
state = json.loads(pathlib.Path('/input/input.json').read_text())
exec(compile(source, '/input/skill.py', 'exec'), namespace)
result = namespace['choose_action'](state)
sys.stdout.write(json.dumps(result, allow_nan=False, separators=(',', ':')))
"""


@dataclass(frozen=True)
class DockerSkillResult:
    output: str = ""
    failure: str | None = None
    elapsed_seconds: float = 0.0
    image_identity: str | None = None


@dataclass(frozen=True)
class DockerSkillExecutor:
    image: str = PINNED_PYTHON_RUNTIME_IMAGE
    docker_binary: str = "docker"

    def execute(
        self, source: str, input_json: str, limits: CandidateLimits, *, cancel: threading.Event | None = None
    ) -> DockerSkillResult:
        started = time.monotonic()
        deadline = started + limits.timeout_seconds
        cancel = cancel if cancel is not None else threading.Event()
        if cancel.is_set():
            return DockerSkillResult(failure="cancelled")
        if self.image != PINNED_PYTHON_RUNTIME_IMAGE or limits.max_memory_mb < 64:
            return DockerSkillResult(failure="unsupported_environment")
        if os.name != "posix" or shutil.which(self.docker_binary) is None:
            return DockerSkillResult(failure="sandbox_unavailable")
        if len(source.encode()) > 65536 or len(input_json.encode()) > 65536:
            return DockerSkillResult(failure="input_limit")
        name = f"autocontext-skill-{uuid.uuid4().hex}"
        environment = sanitized_docker_environment()
        proc: subprocess.Popen[bytes] | None = None
        threads: list[threading.Thread] = []
        image_identity: str | None = None
        created = False
        failure: str | None = None
        output = ""

        def remaining() -> float:
            if cancel.is_set():
                raise InterruptedError("cancelled")
            if time.monotonic() >= deadline:
                raise TimeoutError("timeout")
            return max(0.001, deadline - time.monotonic())

        try:
            # Never pull during execution. Operators prepare this exact image;
            # absent Docker/image/daemon fails closed before candidate execution.
            inspected = subprocess.run(  # noqa: S603
                [self.docker_binary, "image", "inspect", "--format", "{{.Id}}/{{.Architecture}}", self.image],
                capture_output=True, timeout=remaining(), check=False, env=environment,
            )
            if inspected.returncode != 0:
                return DockerSkillResult(failure="sandbox_unavailable", elapsed_seconds=time.monotonic() - started)
            image_identity = inspected.stdout.decode("utf-8").strip()
            with tempfile.TemporaryDirectory(prefix="autocontext-skill-") as temp:
                root = Path(temp).resolve()
                (root / "skill.py").write_text(source, encoding="utf-8")
                (root / "input.json").write_text(input_json, encoding="utf-8")
                (root / "runner.py").write_text(RUNNER, encoding="utf-8")
                command = build_docker_isolation_command(
                    docker_binary=self.docker_binary, image=self.image, container_name=name,
                    labels={"autocontext.skill": "schema-migration-v1"},
                    limits=DockerIsolationLimits(
                        memory_mb=limits.max_memory_mb, cpu_count=1, pids_limit=16,
                        cpu_time_seconds=max(1, math.ceil(limits.timeout_seconds)),
                    ),
                    readonly_mounts={root: "/input"}, writable_mounts={},
                    tmpfs_mounts={"/tmp": "rw,noexec,nosuid,nodev,size=16m,mode=1777"},
                    argv=["python", "-I", "-B", "-S", "/input/runner.py"],
                    auto_remove=False, working_dir="/tmp", ulimits={"nofile": (64, 64)},
                )
                # Create before start so cancellation always has an established
                # container identity to remove; never leave a running docker run.
                remaining()
                created = True
                creation = subprocess.run(  # noqa: S603
                    [command[0], "create", *command[2:]], capture_output=True,
                    check=False, timeout=remaining(), env=environment,
                )
                if creation.returncode != 0:
                    raise RuntimeError("container_create_failed")
                remaining()
                proc = subprocess.Popen(  # noqa: S603
                    [self.docker_binary, "start", "--attach", name], stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, close_fds=True,
                    start_new_session=True, env=environment,
                )
                assert proc.stdout is not None and proc.stderr is not None
                quota = threading.Event()
                stdout, stderr = BoundedOutput(), BoundedOutput()
                wire = bytearray()

                def observe(chunk: bytes) -> None:
                    wire.extend(chunk[: max(0, limits.max_output_bytes + 1 - len(wire))])

                for stream, capture, observer in ((proc.stdout, stdout, observe), (proc.stderr, stderr, None)):
                    thread = threading.Thread(
                        target=drain_bounded,
                        # The main loop observes quota immediately and removes
                        # the container. Killing an attach client alone cannot
                        # terminate its remote workload.
                        args=(stream, limits.max_output_bytes, capture, quota, lambda: None, observer),
                        daemon=True,
                    )
                    thread.start()
                    threads.append(thread)
                while proc.poll() is None:
                    remaining()
                    if quota.is_set():
                        break
                    cancel.wait(min(0.02, remaining()))
                if quota.is_set():
                    failure = "output_limit"
                elif cancel.is_set():
                    failure = "cancelled"
                elif time.monotonic() >= deadline:
                    failure = "timeout"
                elif proc.returncode != 0:
                    failure = "candidate_error"
                # Cleanup below closes all pipes before decoding the bounded wire.
        except (TimeoutError, subprocess.TimeoutExpired):
            failure = "timeout"
        except InterruptedError:
            failure = "cancelled"
        except (OSError, RuntimeError, ValueError):
            failure = "sandbox_error"
        finally:
            if created:
                try:
                    subprocess.run(  # noqa: S603
                        [self.docker_binary, "rm", "--force", name], check=False, capture_output=True,
                        timeout=5, env=environment,
                    )
                    # A failed inspect can mean a daemon error. A successful
                    # empty ps result is positive evidence that removal finished.
                    check = subprocess.run(  # noqa: S603
                        [self.docker_binary, "ps", "--all", "--quiet", "--filter", f"name=^/{name}$"],
                        check=False, capture_output=True, timeout=5, env=environment,
                    )
                    if check.returncode != 0 or check.stdout.strip():
                        failure = "cleanup_unverified"
                except (OSError, subprocess.TimeoutExpired):
                    failure = "cleanup_unverified"
            if proc is not None:
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    failure = "cleanup_unverified"
                    try:
                        proc.kill()
                        proc.wait(timeout=2)
                    except (OSError, subprocess.TimeoutExpired):
                        pass  # The result already records failed cleanup.
                for thread in threads:
                    thread.join(timeout=2)
                if any(thread.is_alive() for thread in threads):
                    failure = "cleanup_unverified"
                for pipe in (proc.stdout, proc.stderr):
                    if pipe is not None:
                        pipe.close()
                if stdout.exceeded or stderr.exceeded:
                    failure = failure or "output_limit"
                elif stdout.read_failed or stderr.read_failed:
                    failure = failure or "output_read_failed"
                if failure is None:
                    try:
                        output = bytes(wire).decode("utf-8", errors="strict")
                    except UnicodeDecodeError:
                        failure = "invalid_output_encoding"
        return DockerSkillResult(output, failure, time.monotonic() - started, image_identity)
