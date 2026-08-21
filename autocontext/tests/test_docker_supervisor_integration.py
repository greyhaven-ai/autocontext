from __future__ import annotations

import hashlib
import json
import os
import secrets
import select
import shutil
import subprocess
import time
import uuid
from pathlib import Path

import pytest

from autocontext.kernel_evolution import docker_supervisor as supervisor
from autocontext.kernel_evolution.docker_worker_runtime import copy_live_tmpfs_report


@pytest.mark.skipif(
    os.environ.get("AUTOCONTEXT_RUN_CPU_DOCKER_INTEGRATION") != "1",
    reason="requires an explicit local Docker release-gate environment",
)
def test_real_docker_supervisor_copies_tmpfs_report_before_ack(tmp_path: Path) -> None:
    """Catch regressions that attempt to copy tmpfs only after the container stops."""

    docker_binary = shutil.which(os.environ.get("AUTOCONTEXT_DOCKER_BINARY", "docker"))
    if docker_binary is None:
        pytest.skip("Docker CLI is unavailable")
    image = os.environ.get("AUTOCONTEXT_CPU_DOCKER_IMAGE", "python:3.12-slim-bookworm")
    inspected_image = subprocess.run(  # noqa: S603
        [docker_binary, "image", "inspect", "--format", "{{.Id}}", image],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if inspected_image.returncode != 0:
        pytest.skip(f"local CPU integration image is unavailable: {image}")
    image_id = inspected_image.stdout.strip()
    assert image_id.startswith("sha256:")

    adapter = tmp_path / "adapter.py"
    adapter.write_text(
        """
import json
import os
import signal
import sys
from pathlib import Path

try:
    descriptor = os.open("/proc/1/fd/0", os.O_RDONLY | os.O_NONBLOCK)
except OSError:
    supervisor_stdin = "blocked"
else:
    os.close(descriptor)
    supervisor_stdin = "readable"
os.kill(1, signal.SIGINT)
workspace_artifact = Path("/workspace/artifact.bin")
workspace_artifact.write_bytes(b"x" * (128 * 1024))
Path(sys.argv[1]).write_text(
    json.dumps({
        "supervisor_stdin": supervisor_stdin,
        "value": 42,
        "workspace_artifact_bytes": workspace_artifact.stat().st_size,
    }),
    encoding="utf-8",
)
print("candidate-stdout")
""",
        encoding="utf-8",
    )
    supervisor_path = Path(supervisor.__file__).resolve(strict=True)
    report_path = tmp_path / "copied-report.json"
    container_name = f"autoctx-supervisor-cpu-{uuid.uuid4().hex[:16]}"
    execution_deadline_ns = time.time_ns() + 15_000_000_000
    hard_deadline_ns = execution_deadline_ns + 10_000_000_000
    uid = os.getuid()
    gid = os.getgid()
    create_command = [
        docker_binary,
        "create",
        "--pull",
        "never",
        "--interactive",
        "--name",
        container_name,
        "--log-driver",
        "none",
        "--read-only",
        "--network",
        "none",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--pids-limit",
        "32",
        "--memory",
        "128m",
        "--memory-swap",
        "128m",
        "--cpus",
        "1",
        "--tmpfs",
        f"/output:rw,noexec,nosuid,nodev,size=65536,nr_inodes=16,mode=0700,uid={uid},gid={gid}",
        "--tmpfs",
        f"/tmp:rw,noexec,nosuid,nodev,size=16m,nr_inodes=128,mode=0700,uid={uid},gid={gid}",
        "--tmpfs",
        f"/workspace:rw,nosuid,nodev,exec,size=8m,nr_inodes=128,mode=0700,uid={uid},gid={gid}",
        "--mount",
        f"type=bind,src={supervisor_path},dst=/autocontext-docker-supervisor.py,readonly",
        "--mount",
        f"type=bind,src={adapter},dst=/adapter.py,readonly",
        "--user",
        f"{uid}:{gid}",
        image_id,
        "env",
        "-i",
        "LANG=C.UTF-8",
        "HOME=/tmp",
        "PATH=/usr/local/bin:/usr/bin:/bin",
        "python",
        "-I",
        "-B",
        "-S",
        "/autocontext-docker-supervisor.py",
        "--supervise",
        "--report",
        "/output/report.json",
        "--max-report-bytes",
        "65536",
        "--execution-deadline-ns",
        str(execution_deadline_ns),
        "--hard-deadline-ns",
        str(hard_deadline_ns),
        "--",
        "python",
        "/adapter.py",
        "/output/report.json",
    ]

    process: subprocess.Popen[bytes] | None = None
    try:
        created = subprocess.run(create_command, check=False, capture_output=True, text=True, timeout=10)  # noqa: S603
        assert created.returncode == 0, created.stderr
        process = subprocess.Popen(  # noqa: S603
            [docker_binary, "start", "--attach", "--interactive", container_name],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        assert process.stdin is not None and process.stdout is not None and process.stderr is not None
        secret = secrets.token_bytes(32)
        collector = supervisor.DockerSupervisorStatusCollector(secret, max_report_bytes=65536)
        stdout = bytearray()
        stderr = bytearray()
        process.stdin.write(supervisor.encode_start(secret))
        process.stdin.flush()

        while collector.completion is None and time.time_ns() < hard_deadline_ns:
            readable, _, _ = select.select([process.stdout, process.stderr], [], [], 0.1)
            for stream in readable:
                chunk = os.read(stream.fileno(), 64 * 1024)
                if stream is process.stdout:
                    stdout.extend(chunk)
                    collector.feed(chunk)
                else:
                    stderr.extend(chunk)
            if process.poll() is not None:
                break

        completion = collector.completion
        assert completion is not None, stderr.decode("utf-8", errors="replace")
        assert completion.report_size is not None, (
            completion,
            stdout.decode("utf-8", errors="replace"),
            stderr.decode("utf-8", errors="replace"),
        )
        assert process.poll() is None, "supervisor exited before the host acknowledged report extraction"
        running = subprocess.run(  # noqa: S603
            [docker_binary, "inspect", "--format", "{{.State.Running}}", container_name],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert running.stdout.strip() == "true"
        copy_live_tmpfs_report(
            docker_binary=docker_binary,
            container_name=container_name,
            report_path=report_path,
            container_python=completion.supervisor_python,
            max_report_bytes=65536,
            timeout_seconds=10,
        )
        report_bytes = report_path.read_bytes()
        assert completion.report_size == len(report_bytes)
        assert completion.report_sha256 == hashlib.sha256(report_bytes).hexdigest()
        assert json.loads(report_bytes) == {
            "supervisor_stdin": "blocked",
            "value": 42,
            "workspace_artifact_bytes": 128 * 1024,
        }

        process.stdin.write(supervisor.encode_ack(secret, completion))
        process.stdin.flush()
        process.stdin.close()
        assert process.wait(timeout=10) == 0
        stdout.extend(process.stdout.read())
        stderr.extend(process.stderr.read())
        assert b"candidate-stdout" in stdout
        assert secret.hex().encode("ascii") not in stdout + stderr

        report_path.unlink()
        with pytest.raises(RuntimeError, match="report extraction failed"):
            copy_live_tmpfs_report(
                docker_binary=docker_binary,
                container_name=container_name,
                report_path=report_path,
                container_python=completion.supervisor_python,
                max_report_bytes=65536,
                timeout_seconds=10,
            )
        assert not report_path.exists()
    finally:
        if process is not None and process.poll() is None:
            process.kill()
            process.wait(timeout=10)
        subprocess.run(  # noqa: S603
            [docker_binary, "rm", "-f", container_name],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
