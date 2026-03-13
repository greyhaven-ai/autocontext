"""SSH client for trusted remote command execution and file transfer."""

from __future__ import annotations

import logging
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from autocontext.integrations.ssh.config import SSHHostConfig

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class SSHCommandResult:
    """Result of a remote SSH command execution."""

    exit_code: int
    stdout: str
    stderr: str
    duration_ms: int

    @property
    def success(self) -> bool:
        return self.exit_code == 0


class SSHClient:
    """Client for executing commands and transferring files over SSH.

    Uses the system ``ssh`` and ``scp`` binaries. Designed for trusted,
    user-owned machines where the operator has configured key-based auth.
    """

    def __init__(self, config: SSHHostConfig) -> None:
        self.config = config

    def _ssh_base_args(self) -> list[str]:
        """Build common SSH flags."""
        args = [
            "ssh",
            "-o", "BatchMode=yes",
            "-o", f"ConnectTimeout={self.config.connect_timeout}",
            "-o", "StrictHostKeyChecking=accept-new",
        ]
        if self.config.port != 22:
            args.extend(["-p", str(self.config.port)])
        if self.config.identity_file:
            args.extend(["-i", self.config.identity_file])
        return args

    def _ssh_target(self) -> str:
        """Return user@host or just host."""
        if self.config.user:
            return f"{self.config.user}@{self.config.hostname}"
        return self.config.hostname

    def _scp_base_args(self) -> list[str]:
        """Build common SCP flags."""
        args = [
            "scp",
            "-o", "BatchMode=yes",
            "-o", f"ConnectTimeout={self.config.connect_timeout}",
            "-o", "StrictHostKeyChecking=accept-new",
        ]
        if self.config.port != 22:
            args.extend(["-P", str(self.config.port)])
        if self.config.identity_file:
            args.extend(["-i", self.config.identity_file])
        return args

    def _scp_target(self, remote_path: str) -> str:
        """Return user@host:path or host:path."""
        if self.config.user:
            return f"{self.config.user}@{self.config.hostname}:{remote_path}"
        return f"{self.config.hostname}:{remote_path}"

    def _wrap_command(self, command: str) -> str:
        """Wrap command with environment variables and working directory."""
        parts: list[str] = []
        if self.config.environment:
            for key, value in sorted(self.config.environment.items()):
                parts.append(f"{key}='{value}'")
        parts.append(command)
        return " ".join(parts)

    def execute_command(self, command: str, *, timeout: float | None = None) -> SSHCommandResult:
        """Execute a command on the remote host via SSH."""
        effective_timeout = timeout or self.config.command_timeout
        wrapped = self._wrap_command(command)
        args = self._ssh_base_args() + [self._ssh_target(), wrapped]

        logger.info("ssh %s: %s", self.config.name, command[:80])
        t0 = time.monotonic()

        try:
            proc = subprocess.run(
                args,
                capture_output=True,
                text=True,
                timeout=effective_timeout,
            )
            duration_ms = int((time.monotonic() - t0) * 1000)
            return SSHCommandResult(
                exit_code=proc.returncode,
                stdout=proc.stdout,
                stderr=proc.stderr,
                duration_ms=duration_ms,
            )
        except subprocess.TimeoutExpired:
            duration_ms = int((time.monotonic() - t0) * 1000)
            return SSHCommandResult(
                exit_code=-1,
                stdout="",
                stderr=f"SSH command timed out after {effective_timeout:.0f}s",
                duration_ms=duration_ms,
            )

    def health_check(self) -> dict[str, Any]:
        """Run a lightweight health check on the remote host."""
        result = self.execute_command("hostname", timeout=float(self.config.connect_timeout))
        if result.exit_code == -1:
            return {"status": "unreachable", "host": self.config.hostname, "error": result.stderr}
        if result.exit_code != 0:
            return {"status": "error", "host": self.config.hostname, "error": result.stderr, "exit_code": result.exit_code}
        return {"status": "healthy", "host": self.config.hostname, "hostname": result.stdout.strip()}

    def upload_file(self, local_path: Path, remote_path: str) -> None:
        """Upload a local file to the remote host via SCP."""
        args = self._scp_base_args() + [str(local_path), self._scp_target(remote_path)]
        proc = subprocess.run(args, capture_output=True, text=True, timeout=self.config.command_timeout)
        if proc.returncode != 0:
            raise RuntimeError(f"SCP upload failed: {proc.stderr.strip()}")

    def download_file(self, remote_path: str, local_path: Path) -> None:
        """Download a file from the remote host via SCP."""
        args = self._scp_base_args() + [self._scp_target(remote_path), str(local_path)]
        proc = subprocess.run(args, capture_output=True, text=True, timeout=self.config.command_timeout)
        if proc.returncode != 0:
            raise RuntimeError(f"SCP download failed: {proc.stderr.strip()}")

    def ensure_working_directory(self) -> None:
        """Create the working directory on the remote host if needed."""
        self.execute_command(f"mkdir -p {self.config.working_directory}")
