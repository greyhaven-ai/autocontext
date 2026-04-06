"""Environment snapshot redaction (AC-503)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath, PureWindowsPath

from autocontext.bootstrap.snapshot import EnvironmentSnapshot


@dataclass(slots=True)
class RedactionConfig:
    """Controls which fields are redacted in the snapshot."""

    redact_hostname: bool = True
    redact_username: bool = True
    redact_paths: bool = True


_REDACTED = "[REDACTED]"


def redact_snapshot(snapshot: EnvironmentSnapshot, config: RedactionConfig | None = None) -> EnvironmentSnapshot:
    """Return a new snapshot with sensitive fields replaced per config."""
    if config is None:
        config = RedactionConfig()

    redacted_fields: list[str] = []
    hostname = snapshot.hostname
    username = snapshot.username
    working_directory = snapshot.working_directory
    shell = snapshot.shell
    notable_files = list(snapshot.notable_files)

    if config.redact_hostname and hostname:
        hostname = _REDACTED
        redacted_fields.append("hostname")

    if config.redact_username and username:
        username = _REDACTED
        redacted_fields.append("username")

    if config.redact_paths and working_directory:
        # Strip absolute path prefix → relative
        prefix = snapshot.working_directory
        working_directory = "."
        notable_files = [_strip_prefix(f, prefix) for f in notable_files]
        redacted_fields.append("working_directory")
    if config.redact_paths and shell:
        redacted_shell = _redact_path_like(shell)
        if redacted_shell != shell:
            shell = redacted_shell
            redacted_fields.append("shell")

    return EnvironmentSnapshot(
        working_directory=working_directory,
        os_name=snapshot.os_name,
        os_version=snapshot.os_version,
        shell=shell,
        hostname=hostname,
        username=username,
        python_version=snapshot.python_version,
        available_runtimes=dict(snapshot.available_runtimes),
        installed_packages=list(snapshot.installed_packages),
        lockfiles_found=list(snapshot.lockfiles_found),
        notable_files=notable_files,
        directory_count=snapshot.directory_count,
        file_count=snapshot.file_count,
        git_branch=snapshot.git_branch,
        git_commit=snapshot.git_commit,
        git_dirty=snapshot.git_dirty,
        git_worktree=snapshot.git_worktree,
        memory_total_mb=snapshot.memory_total_mb,
        memory_available_mb=snapshot.memory_available_mb,
        disk_free_gb=snapshot.disk_free_gb,
        cpu_count=snapshot.cpu_count,
        collected_at=snapshot.collected_at,
        collector_version=snapshot.collector_version,
        redacted_fields=redacted_fields,
    )


def _strip_prefix(path: str, prefix: str) -> str:
    """Strip absolute path prefix, replacing with relative."""
    if path.startswith(prefix):
        stripped = path[len(prefix) :]
        return f".{stripped}" if stripped.startswith("/") else f"./{stripped}"
    return path


def _redact_path_like(value: str) -> str:
    """Collapse an absolute path to its basename while preserving tool identity."""
    posix = PurePosixPath(value)
    if posix.is_absolute():
        return posix.name
    windows = PureWindowsPath(value)
    if windows.is_absolute():
        return windows.name
    return value
