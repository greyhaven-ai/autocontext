"""Environment snapshot domain model (AC-503)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class PackageInfo:
    """An installed Python package."""

    name: str
    version: str


@dataclass(slots=True)
class EnvironmentSnapshot:
    """Structured snapshot of the runtime environment."""

    # Core
    working_directory: str
    os_name: str
    os_version: str
    shell: str
    hostname: str
    username: str

    # Runtimes
    python_version: str
    available_runtimes: dict[str, str]

    # Packages
    installed_packages: list[PackageInfo]
    lockfiles_found: list[str]

    # Filesystem
    notable_files: list[str]
    directory_count: int
    file_count: int

    # Git
    git_branch: str | None
    git_commit: str | None
    git_dirty: bool
    git_worktree: bool

    # System
    memory_total_mb: int
    memory_available_mb: int
    disk_free_gb: float
    cpu_count: int

    # Meta
    collected_at: str
    collector_version: str = "1.0.0"
    redacted_fields: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to JSON-safe dict."""
        return {
            "working_directory": self.working_directory,
            "os_name": self.os_name,
            "os_version": self.os_version,
            "shell": self.shell,
            "hostname": self.hostname,
            "username": self.username,
            "python_version": self.python_version,
            "available_runtimes": dict(self.available_runtimes),
            "installed_packages": [{"name": p.name, "version": p.version} for p in self.installed_packages],
            "lockfiles_found": list(self.lockfiles_found),
            "notable_files": list(self.notable_files),
            "directory_count": self.directory_count,
            "file_count": self.file_count,
            "git_branch": self.git_branch,
            "git_commit": self.git_commit,
            "git_dirty": self.git_dirty,
            "git_worktree": self.git_worktree,
            "memory_total_mb": self.memory_total_mb,
            "memory_available_mb": self.memory_available_mb,
            "disk_free_gb": self.disk_free_gb,
            "cpu_count": self.cpu_count,
            "collected_at": self.collected_at,
            "collector_version": self.collector_version,
            "redacted_fields": list(self.redacted_fields),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EnvironmentSnapshot:
        """Deserialize from dict."""
        packages = [PackageInfo(name=p["name"], version=p["version"]) for p in data.get("installed_packages", [])]
        return cls(
            working_directory=data["working_directory"],
            os_name=data["os_name"],
            os_version=data["os_version"],
            shell=data["shell"],
            hostname=data["hostname"],
            username=data["username"],
            python_version=data["python_version"],
            available_runtimes=data.get("available_runtimes", {}),
            installed_packages=packages,
            lockfiles_found=data.get("lockfiles_found", []),
            notable_files=data.get("notable_files", []),
            directory_count=data.get("directory_count", 0),
            file_count=data.get("file_count", 0),
            git_branch=data.get("git_branch"),
            git_commit=data.get("git_commit"),
            git_dirty=data.get("git_dirty", False),
            git_worktree=data.get("git_worktree", False),
            memory_total_mb=data.get("memory_total_mb", 0),
            memory_available_mb=data.get("memory_available_mb", 0),
            disk_free_gb=data.get("disk_free_gb", 0.0),
            cpu_count=data.get("cpu_count", 0),
            collected_at=data["collected_at"],
            collector_version=data.get("collector_version", "1.0.0"),
            redacted_fields=data.get("redacted_fields", []),
        )
